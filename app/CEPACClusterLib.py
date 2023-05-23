# -*- coding: utf-8 -*-
"""
Created on Tue Sep 29 10:10:08 2015
CEPAC Cluster tool library

STD list:
* objectify things
* GUI
* make zipped download work
* fix jobname and folder name thing
* kill jobs

@author: Taige Hou (thou1@partners.org)
@author: Kai Hoeffner (khoeffner@mgh.harvard.edu)
Updated from Python 2.7 to 3.10 on Fri Jan 20 2023 by Aaron Wu (aawu@mgh.harvard.edu)
"""
from __future__ import print_function
import os
import glob
import paramiko
# md5 library caused issues with building the tool; the code that uses md5 may need to be altered or removed
import hashlib  # contains the md5 submodule
#from hashlib import md5  # had to change from just "import md5" since md5 is now a submodule of hashlib in python 3+
import re
# import zipfile  # Don't need zipfile anymore as the download as zip functionality was removed in the previous version
import threading
import time
from stat import S_ISDIR
from datetime import datetime
from datetime import timedelta
from timeit import default_timer as timer

import wx

# A list of clusters
CLUSTER_NAMES = ("ERISOne", "ERISTwo", "Custom")
COMMAND_LANGUAGES = ("LSF", "SLURM")
# Maximum number of concurrent connections
MAX_CONNECTIONS = 8

# Mapping of cluster names to hostname, runfolder path and model folder path
# For run_folder use only relative path from home directory (this is required as lsf and cepac are picky about paths)
# For model_folder can use either absolute path or relative path from home directory
# do not use ~ in path to represent home directory as the ftp client cannot find the directory

# have pre-defined inputs for each hostname
CLUSTER_INFO = {"ERISOne": {'host': 'erisone.partners.org',
                        'run_folder': 'runs',
                        'model_folder': '/data/cepac/modelVersions',
                        'default_queues': ("medium", "long", "vlong", "big")},
                "ERISTwo": {'host': 'eristwo.partners.org',
                        'run_folder': 'runs',
                        'model_folder': '/data/cepac/modelVersions',
                        'default_queues': ("normal", "bigmem")},
                "Custom": {'host': '',
                           'run_folder': 'runs',
                           'model_folder': '',
                           'default_queues': ()},
                }

# ---------------------------------------------


class CheckConnectionThread(threading.Thread):
    """Thread used to check if the SSH connection is still valid"""
    def __init__(self, cluster):
        threading.Thread.__init__(self)
        self.cluster = cluster
        self.abort = False

    def stop(self):
        self.abort = True

    def run(self):
        start = timer()
        self.abort = False
        while self.cluster.num_connections >= MAX_CONNECTIONS:
            time.sleep(.2)
        self.cluster.num_connections += 1
        while not self.abort:
            connection_secure = self.cluster.connection_secure()
            if not connection_secure:
                time.sleep(3)
                if not connection_secure:
                    self.cluster.dialog_output("SSH Connection lost.")
                    self.cluster.output("WARNING: SSH Connection was lost. PLEASE CHECK CONNECTION OR LOG IN AGAIN")
                    self.stop()
            elif (timer() - start) > 3600:  # 1 hour
                self.stop()
            else:
                pass
            time.sleep(10)

        self.cluster.num_connections -= 1


""" this function adds the relevant parameters to the UploadThread object """


class UploadThread(threading.Thread):
    """Thread used to upload runs and submit jobs"""
    def __init__(self, cluster, dir_local, dir_remote, lsfinfo, update_func, glob_pattern=("*.in",)):
        threading.Thread.__init__(self)
        self.cluster = cluster
        self.args = [self, dir_local, dir_remote, lsfinfo, update_func, glob_pattern]
        self.abort = False

    def stop(self):
        self.abort = True

    def run(self):
        while self.cluster.num_connections >= MAX_CONNECTIONS:
            time.sleep(.2)  # if more connections than maximum, wait 0.2 seconds before seeing if still more than max, allow other threads to continue
        self.cluster.num_connections += 1
        jobfiles = self.cluster.sftp_upload(*self.args)
        if not self.abort:
            self.cluster.pybsub(jobfiles)
            self.cluster.local_job_info_file(self.args[1])

        self.cluster.write_job_info_text_file(self.args[1], self.args[2])
        self.cluster.num_connections -= 1


# ---------------------------------------------
class DownloadThread(threading.Thread):
    """Thread used to download runs"""

    def __init__(self, cluster, run_folder, dir_remote, dir_local, update_func):
        threading.Thread.__init__(self)
        self.cluster = cluster
        self.args = [self, dir_remote, dir_local, update_func]
        self.abort = False
        self.run_folder = run_folder
        # Total number of files to download
        self.total_files = 0
        # current progress of download
        self.curr_files = 0

    def stop(self):
        self.abort = True

    def run(self):
        while self.cluster.num_connections >= MAX_CONNECTIONS:
            time.sleep(.2)

        # counts total number of files in folder recursively
        stdin, stdout, stderr = self.cluster.ssh.exec_command("find {} -type f | wc -l"
                                                              .format(
            clean_path(self.cluster.run_path + "/" + self.run_folder)))
        # wait for command to finish
        stdout.channel.recv_exit_status()
        self.total_files = int(stdout.read().strip())
        if self.total_files == 0:
            self.total_files = 1
        self.cluster.num_connections += 1
        # self.cluster.sftp_get_compressed(*self.args)
        self.cluster.sftp_get_recursive(*self.args)
        self.cluster.num_connections -= 1


# ---------------------------------------------
class JobInfoThread(threading.Thread):
    """Thread used to get detailed job info"""

    def __init__(self, cluster, jobid, post_func):
        threading.Thread.__init__(self)
        self.cluster = cluster
        self.jobid = jobid
        # function which tells thread how to post results
        self.post_func = post_func

    def run(self):
        while self.cluster.num_connections >= MAX_CONNECTIONS:
            time.sleep(.2)
        self.cluster.num_connections += 1
        job_info = self.cluster.get_job_info(self.jobid)
        self.post_func(jobid=self.jobid, data=job_info)
        self.cluster.num_connections -= 1


# ---------------------------------------------
class CEPACClusterApp:
    """Basic class for the desktop interface with the CEPAC cluster"""

    def __init__(self, ):
        self.port = 22

        # SSH Client
        self.ssh = paramiko.SSHClient()

        # Dictionary of available model versions with model type as keys
        self.model_versions = None
        # List of available run queues
        self.queues = ["Short", "Medium", "Long", "VLong"]
        # number of currently open connections
        self.num_connections = 0
        # thread for checking status of ssh connection
        self.connection_thread = None
        # thread for uploading
        self.upload_thread = None
        # threads for downloads
        self.download_threads = []
        # scheduler used (LSF, SLURM)
        self.scheduler = "LSF"

    def bind_output(self, output=print):
        """
        output is a function used to write messages from the app.
        Defaults to the print function for the console version.
        Any calls to print should use the self.output function instead
        """
        self.output = output

        # print initiation message
        self.output("=" * 40, False)
        self.output("Initiating Cepac Cluster App", False)

    def bind_dialog(self, output=print):
        self.dialog_output = output

    """
    def bind_duplicate_warning_dialog(self, output=print):
        self.duplicate_warning_dialog_output = output
    """

    def connect(self, hostname='erisone.partners.org',
                username=None, password=None,
                run_path=None, model_path=None, clustername=None, scheduler=None):
        """
        Starts connection to host.
        Should be called once per client.
        """
        # Close any previous connections
        self.close_connection()

        # Need to convert to string for paramiko because input could be unicode
        self.hostname = str(hostname)
        self.username = str(username)
        self.password = str(password)
        self.run_path = str(run_path)
        self.model_path = str(model_path)
        self.clustername = str(clustername)
        self.scheduler = str(scheduler)

        self.output("\nConnecting to {} as user: {}...".format(self.hostname, self.username), False)

        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.ssh.connect(self.hostname, port=22, username=self.username, password=self.password)
        except paramiko.AuthenticationException:
            # Login failed
            self.output("\tAuthentication Error. Check username and password.", False)
        #except Exception as err:
            #self.output("\tNetwork error. Check network connection or VPN.", False)
        else:
            # Get model and queue information
            self.output("\tLogin Succesful", False)
            self.update_cluster_information()

    def create_connection_thread(self):
        self.connection_thread = CheckConnectionThread(self)
        self.connection_thread.daemon = True
        self.connection_thread.start()

    def create_upload_thread(self, *args, **kwargs):
        """Calls on the start() function in UploadThread to start the thread."""
        self.upload_thread = UploadThread(self, *args, **kwargs)
        self.upload_thread.daemon = True
        self.upload_thread.start()

    def create_download_thread(self, *args, **kwargs):
        """Calls on the start() function in DownloadThread to start the thread"""
        thread = DownloadThread(self, *args, **kwargs)
        thread.daemon = True
        thread.start()

    def sftp_get_recursive(self, thread, dir_remote, dir_local, progress_func, sftp=None):
        """Recursively Downloads folder including subfolders"""
        if not sftp:
            # Create sftp object. Should only do this once per download.
            with paramiko.Transport((self.hostname, self.port)) as t:
                t.connect(username=self.username, password=self.password)
                t.use_compression()
                # recomended window size from https://github.com/paramiko/paramiko/issues/175
                t.window_size = 134217727

                sftp = t.open_session()
                sftp = paramiko.SFTPClient.from_transport(t)
                progress_func(0, thread.run_folder)
                self.output("\nDownloading from folder {} to folder {}...".format(dir_remote, dir_local))
                self.sftp_get_recursive(thread, dir_remote, dir_local, progress_func, sftp)
                self.output("\tDownload Complete")
        else:
            item_list = sftp.listdir(dir_remote)
            dir_local = str(dir_local)
            dir_local = os.path.join(dir_local, os.path.basename(dir_remote))

            if not os.path.isdir(dir_local):
                os.makedirs(dir_local)

            for item in item_list:
                item = str(item)

                if isdir(dir_remote + "/" + item, sftp):
                    self.sftp_get_recursive(thread, dir_remote + "/" + item, dir_local, progress_func, sftp)
                else:
                    sftp.get(dir_remote + "/" + item, os.path.join(dir_local, item))
                    thread.curr_files += 1
                    progress_func(thread.curr_files / float(thread.total_files) * 100, thread.run_folder)

    #    def sftp_get_compressed(self, dir_remote, dir_local, sftp = None):
    #        """Download everything as one file"""
    #        compfile = "compfile.tar.gz"
    #        self.output("\nCompressing {}".format(dir_remote))
    #        stdin, stdout, stderr = self.ssh.exec_command("tar -zcf ~/{} ~/{} ".format(compfile, dir_remote))
    #
    #
    #        if not stdout.readlines():
    #            #Create sftp object
    #            with paramiko.Transport((self.hostname, self.port)) as t:
    #                t.connect(username=self.username, password=self.password)
    #                t.use_compression()
    #                #recomended window size from https://github.com/paramiko/paramiko/issues/175
    #                t.window_size = 134217727
    #                sftp = t.open_session()
    #                sftp = paramiko.SFTPClient.from_transport(t)
    #                # This should be stored somewhere locally for faster access!
    #                dir_temp_local='C:\Temp'
    #
    #                sftp.get("~/{}".format(compfile), dir_temp_local+"\CEPACclusterdownload.zip")
    #                self.output("\nDownload complete!")
    #
    #            stdin, stdout, stderr = ssh.exec_command("rm {}".format(compfile))
    #            self.output("\nExtracting files")
    #            with zipfile.ZipFile(dir_temp_local+"CEPACclusterdownload.zip", "r") as z:
    #                z.extractall(dir_local)
    #        else:
    #            self.output(stdout.readlines())


    def sftp_upload(self, thread, dir_local, dir_remote, lsfinfo, progress_func, glob_pattern=("*.in",)):
        """Uploads local directory to remote server and generates a job file per subfolder and returns the list of job files."""
        files_copied = 0
        jobfiles = []

        # Create sftp object
        with paramiko.Transport((self.hostname, self.port)) as t:
            t.connect(username=self.username, password=self.password)
            t.use_compression()
            # recomended window size from https://github.com/paramiko/paramiko/issues/175
            t.window_size = 134217727
            sftp = t.open_session()
            sftp = paramiko.SFTPClient.from_transport(t)

            # Checking if ssh connection is good. If not, outputs error message and stops the upload function.
            if not self.connection_secure():
                self.output("\nERROR: SSH Connection Failed. Please try again. If problem persists, please log in again"
                            " or check your connection.")
                return

            self.output("\nSubmitting runs from folder {} ...".format(dir_local))
            # list of tuples (local_file, remote file) that will be uploaded
            files_to_upload = []
            for dirpath, dirnames, filenames in os.walk(dir_local):
                matching_files = []
                for pattern in glob_pattern:
                    matching_files.extend([f for f in glob.glob(dirpath + os.sep + pattern) if not os.path.isdir(f)])

                if not matching_files:
                    continue
                matching_files = [x for x in matching_files if "~$" not in x]  # remove temp files that have "$" in name
                # Fix foldername
                remote_base = dir_remote + '/' + os.path.basename(dir_local)
                if not os.path.relpath(dirpath, dir_local) == '.':
                    curr_dir_remote = remote_base + '/' + os.path.relpath(dirpath, dir_local).replace("\\", "/")
                else:
                    curr_dir_remote = remote_base

                # Create folder and subfolders
                stdin, stdout, stderr = self.ssh.exec_command("mkdir -p '{}'".format(curr_dir_remote))
                # wait for command to finish
                stdout.channel.recv_exit_status()
                self.output("\tCreating {}".format(curr_dir_remote))

                self.output("\tCopying model from model folder to run folder")

                # Write and collect job files
                if thread.abort:
                    return None
                self.write_jobfile(curr_dir_remote, lsfinfo, sftp)
                if self.scheduler == "LSF":
                    jobfiles.append(curr_dir_remote + '/job.info')
                else:
                    jobfiles.append(curr_dir_remote + '/job.sh')
                self.output('\t Collecting input files...')
                # Upload files
                for fpath in matching_files:
                    is_up_to_date = False
                    fname = os.path.basename(fpath)

                    local_file = fpath
                    remote_file = curr_dir_remote + '/' + fname
                    # if remote file exists
                    try:
                        sftp.stat(remote_file)
                    # do nothing if the file isn't there; otherwise check to see if it's changed
                    except IOError:
                        pass
                    else:
                        local_file_data = open(local_file, "rb").read()
                        remote_file_data = sftp.open(remote_file).read()
                        # code related to md5 may need to be updated due to issues with building the tool 
                        md1 = hashlib.md5(local_file_data).digest()
                        md2 = hashlib.md5(remote_file_data).digest()
                        if md1 == md2:
                            """ if files already in remote folder, set is_up_to_date to true and don't upload again.
                             Prevents repeat run folders"""
                            is_up_to_date = True
                            self.output('\t Already uploaded, using uploaded file: {}'.format(local_file))

                    if not is_up_to_date:
                        files_to_upload.append((local_file, remote_file))

            # upload files
            for local_file, remote_file in files_to_upload:
                if thread.abort:
                    return None
                self.output('\tCopying {} to {}'.format(local_file, remote_file))
                sftp.put(local_file, remote_file)
                files_copied += 1

                # update progress bar
                progress_func(files_copied / float(len(files_to_upload)) * 100)

            self.output('\tFinished Upload')
        return jobfiles

    def write_jobfile(self, curr_dir_remote, lsfinfo, sftp):
        """
        Write job files for the current folder.
        lsfinfo is a dictionary which contains
            queue - the queue to submit to
            email - email address to send upon job completion (optional)
            modeltype - should be either treatm, debug, or transm
            modelversion - name of the model version to run
        """
        if self.scheduler == "SLURM":
            self.output('\tWriting Job file: {}'.format(curr_dir_remote + '/job.sh'))
            with sftp.open(curr_dir_remote + '/job.sh', 'wb') as f:
                jobcommand = "#!/bin/bash\n" + \
                             "#SBATCH --job-name=\"" + lsfinfo['jobname'] + "\"\n" + \
                             "#SBATCH --partition=" + lsfinfo['queue'] + "\n" + \
                             "#SBATCH --chdir={}".format(curr_dir_remote + "\n")

                if 'email' in lsfinfo:
                    jobcommand += "#SBATCH --mail-user=" + lsfinfo['email'] + "\n" + \
                                  "#SBATCH --mail-type=end\n"
                if 'customcommand' in lsfinfo:
                    jobcommand += lsfinfo['customcommand'] + "\n"
                if lsfinfo['modeltype'] not in ["smoking", "covid-19"]:
                    jobcommand += self.model_path + "/" + lsfinfo['modeltype'] + "/" + lsfinfo[
                        'modelversion'] + " ~/" + clean_path(curr_dir_remote)
                    # jobcommand += curr_dir_remote + "/" + lsfinfo['modeltype'] + "/" + lsfinfo['modelversion'] + " ~/" + clean_path(curr_dir_remote)
                else:

                    jobcommand += "/data/cepac/python/bin/python3.6 " + self.model_path + "/" + lsfinfo['modeltype'] + "/" + \
                                  lsfinfo['modelversion'] + "/sim.py" + " ~/" + clean_path(curr_dir_remote)

                f.write(jobcommand)
        # Else if scheduler is LSF, use the LSF commands instead of the SLURM commands
        elif self.scheduler == "LSF":
            self.output('\tWriting Job file: {}'.format(curr_dir_remote + '/job.info'))
            with sftp.open(curr_dir_remote + '/job.info', 'wb') as f:
                jobcommand = "#!/bin/bash\n" + \
                             "#BSUB -J \"" + lsfinfo['jobname'] + "\"\n" + \
                             "#BSUB -q " + lsfinfo['queue'] + "\n"
                if 'email' in lsfinfo:
                    jobcommand += "#BSUB -u " + lsfinfo['email'] + "\n" + \
                                  "#BSUB -N\n"
                if 'customcommand' in lsfinfo:
                    jobcommand += lsfinfo['customcommand'] + "\n"
                if lsfinfo['modeltype'] not in ["smoking", "covid-19"]:
                    jobcommand += self.model_path + "/" + lsfinfo['modeltype'] + "/" + lsfinfo[
                        'modelversion'] + " ~/" + clean_path(curr_dir_remote)
                    # jobcommand += curr_dir_remote + "/" + lsfinfo['modeltype'] + "/" + lsfinfo['modelversion'] + " ~/" + clean_path(curr_dir_remote)
                else:

                    jobcommand += "/data/cepac/python/bin/python3.6 " + self.model_path + "/" + lsfinfo[
                        'modeltype'] + "/" + \
                                  lsfinfo['modelversion'] + "/sim.py" + " ~/" + clean_path(curr_dir_remote)

                f.write(jobcommand)
    # copy_model_to_runfolder not being used currently as python is not on user's runfolder. Too much of a hassle.
    #2023-02-16
    """
    def copy_model_to_runfolder(self, curr_dir_remote, lsfinfo):
        full_model_path = None
        if lsfinfo['modeltype'] not in ["smoking", "covid-19"]:
            full_model_path = self.model_path + "/" + lsfinfo['modeltype'] + "/" + lsfinfo['modelversion']
            curr_dir_model_dir = curr_dir_remote + "/" + lsfinfo['modeltype'] # directory that will contain the model in the runs folder
            stdin, stdout, stderr = self.ssh.exec_command("mkdir {} && cp -r {} {}".format(curr_dir_model_dir,
                                                                                        full_model_path,
                                                                                        curr_dir_model_dir + "/" +
                                                                                        lsfinfo['modelversion']))
        else:
            full_model_path = self.model_path + "/" + lsfinfo['modeltype'] + "/" + \
                              lsfinfo['modelversion'] + "/sim.py"
            print(self.model_path)
            curr_dir_model_dir = curr_dir_remote + "/" + lsfinfo['modeltype']
            stdin, stdout, stderr = self.ssh.exec_command("mkdir {}".format(curr_dir_model_dir))
            stdin, stdout, stderr = self.ssh.exec_command("mkdir {} && cp -r {} {}".format(curr_dir_model_dir +
                                                                                           "/" + lsfinfo[
                                                                                               'modelversion'],
                                                                                        full_model_path,
                                                                                        curr_dir_model_dir +
                                                                                           "/" + lsfinfo['modelversion']))
    """
    def pybsub(self, jobfiles):
        """Submit jobs for job list to LSF. If there is an error with submission, output the error message"""
        errorfilelist = []
        for job in jobfiles:
            if self.scheduler == "LSF":
                stdin, stdout, stderr = self.ssh.exec_command("bash -lc bsub < '{}'".format(job))
            if self.scheduler == "SLURM":
                job_folder = "/".join(job.split("/")[:-1])
                stdin, stdout, stderr = self.ssh.exec_command("bash -lc 'sbatch {}'".format(job))

            stdout.read()
            err = stderr.read()
            if err.strip():
                self.output('Error: {}'.format(err))
                errorfilelist.append(job)
                with open("logfile.txt", 'w') as log:
                    log.write(str(err) + '\n')
                    log.close()
            if not err.strip():
                self.output('\tSubmitted :{}'.format(job))
            # making a new file in the folder containing the runs on the local machine containing run info
            '''
            curr_running_jobs = self.get_job_list()
            curr_running_jobs_ids = []
            for jobs in curr_running_jobs:
                curr_running_jobs_ids.append(jobs[0])
            curr_running_jobs_ids.sort(reverse=True)
            just_submitted_id = curr_running_jobs_ids[0]  # job ID of the just submitted job
            with sftp.open(job, 'wb') as f:
                jobid_addition = "\n Job ID: " + just_submitted_id + "\n"
                f.write(jobid_addition)
            '''
        for n in errorfilelist:
            folder = n.replace("/job.info", "")
            self.output("\tDid not submit {} job due to error. \nRun files can be configured in \"Download\" tab.".format(folder))
        if errorfilelist:
            self.output("TIP: Path names should not exceed 248 characters and filenames should not exceed 260. \nIf an input file path name is too long, it will not submit.")

    """
    def add_to_job_info_file(self, jobfiles):
        # Get job listing and format the result
        stdin, stdout, stderr = self.ssh.exec_command(
            "bash -lc bjobs | awk '{if (NR!=1) print($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)}'")
        # Each entry in Job data will be a list [jobid, status, queue, submit_time]
        job_data = [line.split() for line in stdout.readlines()]
        for i in range(len(job_data)):


        return job_data
    """


    def duplicate_folder(self, dir_local, dir_remote):
        """
            Checks if the folder that is being uploaded has the same name as another folder already in the run folder.
            Returns a boolean.
        """
        stdin, stdout, stderr = self.ssh.exec_command("ls -1 {}".format(dir_remote))
        remote_folders = [i.replace('\n','') for i in stdout.readlines()]
        dir_local_path_list = dir_local.split("\\")
        dir_local_name = dir_local_path_list[-1]
        if dir_local_name in remote_folders:
            return True
        else:
            return False

    def write_job_info_text_file(self, dir_local, dir_remote):
        dir_local_split = dir_local.split("\\") # split the local directory name into a list containing the path components
        dir_local_name = dir_local_split[-1]    # get the last item of the list for the name of the run folder (without path)
        with paramiko.Transport((self.hostname, self.port)) as t:
            t.connect(username=self.username, password=self.password)
            t.use_compression()
            # recomended window size from https://github.com/paramiko/paramiko/issues/175
            t.window_size = 134217727
            sftp = t.open_session()
            sftp = paramiko.SFTPClient.from_transport(t)

            jobids = []
            job_information = []

            for index, job_data in enumerate(self.get_job_list()):
                if self.scheduler == "LSF":
                    """ need the following conditional because if the job is still pending, there is nothing in 
                    the exec_host column (executing host has not been assigned), so there are only 9 items in job_data list.
                    """
                    if len(job_data) < 9:  # if less than 9 items in job_data list (no job name given and pending)
                        jobid, user, status, queue, from_host, submit_month, submit_day, submit_time = job_data
                    elif len(job_data) < 10:  # if less than 10 items in job_data list (while pending)
                        jobid, user, status, queue, from_host, name, submit_month, submit_day, submit_time = job_data
                    else:  # if 10 or more items in job_data list (while running)
                        jobid, user, status, queue, from_host, exec_host, name, submit_month, submit_day, submit_time = job_data
                    full_submit_time = submit_month + " " + submit_day + " " + submit_time
                    jobids.append(jobid)
                    jobname, modelname, run_folder = self.get_job_info(jobid)
                    job_information.append([jobid, run_folder, full_submit_time, jobname, modelname])
                elif self.scheduler == "SLURM":
                    jobid, status, queue = job_data
                    jobids.append(jobid)
                    jobname, modelname, run_folder, submit_time = self.get_job_info(jobid)
                    job_information.append([jobid, run_folder, submit_time, jobname, modelname])

            for dirpath, dirnames, filenames in os.walk(dir_local):
                remote_base = dir_remote + '/' + os.path.basename(dir_local)
                if not os.path.relpath(dirpath, dir_local) == '.':
                    curr_dir_remote = remote_base   + '/' + os.path.relpath(dirpath, dir_local).replace("\\", "/")
                else:
                    curr_dir_remote = remote_base
                curr_dir_name = str(os.path.basename(dir_local))

            for job in job_information:
                job_id, job_run_folder, job_submit_time, job_name, model_name = job
                with sftp.open(self.run_path + '/' + job_run_folder + '/{}info.txt'.format(job_id), 'wb') as f:
                    f.write("Job ID: {} \n".format(job_id))
                    f.write("Job Submit time: {} \n".format(job_submit_time))
                    f.write("Remote Run Folder: {} \n".format(job_run_folder))
                    f.write("Local Path of Input Files: {} \n".format(dir_local))
                    f.write("Job Name: {} \n".format(job_name))
                    f.write("Model Version: {} \n".format(model_name))
                    f.close()

    def local_job_info_file(self, dir_local):
        job_data = self.get_job_list()
        jobids = []
        jobfolders = []
        for job in job_data:
            jobids.append(job[0])
            job_info = self.get_job_info(job[0])
            jobfolders.append(job_info[2])
        dirlist = []
        for dirPath, dirNames, filenames in os.walk(dir_local):
            if len(filenames) > 0:
                folderpath = dirPath, dirNames
                local_run_folder_name = str(folderpath[0])
                local_run_folder_name.replace("\\", "/")
                dirlist.append(local_run_folder_name)  # contains the folder path of each job


    def get_run_folders(self):
        """
        Gets the names of all the folders in the run_folder on the cluster
        and returns as a list of lists, with each sublist containing the folder name and date last modified.
        ie. [[job1, Wed May  3 15:56:26 EDT 2023], [job2, Wed May  3 15:56:26 EDT 2023], .... ].
        """
        self.output("\nRetrieving run folders ...", False)
        # use ls -1 {}| awk  '{$1=$2=""; print 0}' to get long form data but not very useful
        stdin, stdout, stderr = self.ssh.exec_command("ls -l {}".format(self.run_path))
        run_folders_long = stdout.readlines()
        run_folders_with_date = []
        for index, entry in enumerate(run_folders_long):
            if index > 0:  # first output of "ls -l" is not a folder, so skip it.
                folder_data = entry.split()
                modified_date = ' '.join(folder_data[5:8])
                folder_name = ' '.join(folder_data[8:])
                run_folders_with_date.append([folder_name, modified_date])
        self.output("\nFound {} run folders".format(len(run_folders_with_date)))
        return run_folders_with_date


    def get_run_subfolders(self, folder):
        """
        Gets the subpaths of all the subdirectories in the main run folder, if there are any, and
        returns them as a list.
        """
        folder_path = folder
        stdin, stdout, stderr = self.ssh.exec_command("ls -R {}".format(folder_path))
        subfolders = stdout.readlines()
        subfolders = [x for x in subfolders if self.run_path in x]
        return subfolders


    def delete_run_folders(self, folderlist):
        """Deletes the list of folders from the cluster. Called in download tab and status tab (kill and delete)"""
        self.output("\nDeleting Run Folders ...", False)
        for folder in folderlist:
            self.output("\tDeleting {}".format(folder), False)
            stdin, stdout, stderr = self.ssh.exec_command("rm -rf {}".format(self.run_path + "/" + clean_path(folder)))
        self.output("\tFinished Deleting", False)

    def get_job_list(self):
        """
        Gets some basic information about currently running jobs
        Returns jobid, status and queue
        For detailed job info use get_job_info
        """
        # Get job listing and format the result
        job_data = []
        if self.scheduler == "LSF":
            stdin, stdout, stderr = self.ssh.exec_command("bash -lc bjobs | awk '{if (NR!=1) print($1,$2,$3,$4,$5,$6,$7,$8,\
                   $9,$10)}'")
            job_data = [line.split() for line in stdout.readlines()]
        elif self.scheduler == "SLURM":
            #DEBUG
            # stdin, stdout, stderr = self.ssh.exec_command("scontrol show jobs")
            stdin, stdout, stderr = self.ssh.exec_command("squeue -l")
            all_job_data = [line.split() for line in stdout.readlines()]
            for index in range(2, len(all_job_data)):
                job_data.append([all_job_data[index][0], all_job_data[index][4], all_job_data[index][1]])  # list contains jobId, status, queue/partition
            #END DEBUG
        # Each entry in Job data will be a list [jobid, status, queue, submit_time]
        return job_data

    def output_get_job_list(self):
        """
        outputs "Getting job listing ..." to the console output
        """
        self.output("\nGetting job listing ...", False)

    def get_job_info(self, jobid):
        """
        Returns detailed job information by running bjobs -l
        Returns a tuple of (jobname, modelname, runfolder)
        """
        if self.scheduler == "LSF":
            stdin, stdout, stderr = self.ssh.exec_command("bash -lc 'bjobs -l {}'".format(jobid))
            # wait for command to finish
            stdout.channel.recv_exit_status()

            # read job info and get rid of extra spaces
            job_data = re.sub("\n\s*", "", stdout.read().decode('utf-8'))
            # get jobname, modelname, runfolder from job info
            re_pattern = "Job Name <(.*?)>.*" + \
                         "Command <.*?{}/.*?/(.*?)".format(self.model_path) + \
                         "~/{}/(.*?)>".format(self.run_path)

            match = re.search(re_pattern, job_data)  # marker
            if match:
                job_name, model_version, run_folder = match.groups()
                run_folder = reverse_clean_path(run_folder)
                model_version = model_version.strip()
                model_version = model_version.split(
                    '/')  # splits the model version text so that entry point files are separated from the name
                model_version_name = model_version[0]  # the model name (without entry point files) are the first item in the model version split text list
                return job_name, model_version_name, run_folder
            else:
                return None
        elif self.scheduler == "SLURM":
            stdin, stdout, stderr = self.ssh.exec_command("scontrol show jobs -d {}".format(jobid))
            # read here to add delay and avoid being blocked by server
            # wait for command to finish
            stdout.channel.recv_exit_status()

            job_data = [line.split() for line in stdout.readlines()]
            job_info_list = self.convert_scontrol_list_to_jobinfo_list(job_data)
            jobname = job_info_list[0].get("JobName")
            jobfile = job_info_list[0].get("Command")
            run_folder = "/".join(jobfile.split("/")[:-1])  # getting run folder from job file path
            submit_time = job_info_list[0].get("SubmitTime")

            stdin, stdout, stderr = self.ssh.exec_command("tail {}".format(jobfile))
            tail_raw = [line.split() for line in stdout.readlines()]
            tail = self.convert_scontrol_list_to_onelist(tail_raw)
            model_path = tail[-2]
            model_version = model_path.split('/')
            model = model_version[-2] + '/' + model_version[-1]
            return jobname, model, run_folder, submit_time

    def kill_jobs(self, joblist):
        """Kills jobs with jobids given in joblist"""
        self.output("\nKilling Jobs...", False)
        for i in range(len(joblist)):
            if self.scheduler == "SLURM":
                stdin, stdout, stderr = self.ssh.exec_command("scancel {}".format(joblist[i]))
            else:
                stdin, stdout, stderr = self.ssh.exec_command("bash -lc 'bkill {}'".format(joblist[i]))
            stdout.channel.recv_exit_status()
        self.output("\t {} jobs killed".format(len(joblist)), False)

    def update_cluster_information(self):
        """
        Updates the names of all model versions along with model type(debug, treatm, transm)
        Updates the lists of available queues
        Should be called when logging in
        """

        self.output("\tRetrieving model and queue information...", False)
        stdin, stdout, stderr = self.ssh.exec_command("ls -1 {}".format(self.model_path))
        model_types = [m_type.strip() for m_type in stdout.readlines()]
        model_versions = {}
        for m_type in model_types:
            # For each model type get the associated model versions
            stdin, stdout, stderr = self.ssh.exec_command("ls -1 {}".format(self.model_path + "/" + m_type))
            model_versions[m_type] = [m_version.strip() for m_version in stdout.readlines()]
        self.model_versions = model_versions

        # stdin, stdout, stderr = self.ssh.exec_command("ls -1 {}".format(self.model_path))
        # model_types = [m_type.strip() for m_type in stdout.readlines()]
        # model_versions = {}

        # Gets a list of queues by calling bqueues and filtering the output
        if CLUSTER_INFO[self.clustername]['default_queues']:
            self.queues = CLUSTER_INFO[self.clustername]['default_queues']
        else:
            stdin, stdout, stderr = self.ssh.exec_command("sinfo -r")
            self.queues = [q.strip() for q in stdout.readlines()]
        self.output("\tDone", False)

    # Checks whether the SSH connection is secure by sending "ls" command and seeing if there is an error
    def connection_secure(self):
        try:
            stdin, stdout, stderr = self.ssh.exec_command("ls")
        except:
            return False
        stdout.read()
        err = stderr.read()
        # if error with command, connection is not secured. If no error, connection is good.
        if err.strip():
            return False
        else:
            return True

    def close_connection(self):
        self.ssh.close()

    def output_text(self, text):
        self.output("\n", text)

    def __del__(self):
        # closes SSH connection upon exit
        self.close_connection()

    """ get_submit_time() subtracts the time since model submission from the current time to calculate the date/time
    that the job was submitted. This function is used when SLURM commands are used, as there is no default display for
    when a job was submitted, only how long it's been since it was submitted.
    """
    def get_submit_time(self, time_since_submission):
        submit_time_str = ""
        submit_time = None
        now = datetime.now()
        time_since_submission_list = []
        day_split = time_since_submission.split("-")
        if len(day_split) > 1:
            hour_minute_second = day_split[1].split(":")
            days = int(day_split[0])
            hours = int(hour_minute_second[0])
            minutes = int(hour_minute_second[1])
            seconds = int(hour_minute_second[2])
            submit_time = now - timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        else:
            hour_minute_second = time_since_submission.split(":")
            for i in range(0, len(hour_minute_second)):
                time_since_submission_list.append(hour_minute_second[i])
            if len(time_since_submission_list) == 2:
                minutes = int(hour_minute_second[0])
                seconds = int(hour_minute_second[1])
                submit_time = now - timedelta(minutes=minutes, seconds=seconds)
            elif len(time_since_submission_list) == 3:
                hours = int(hour_minute_second[0])
                minutes = int(hour_minute_second[1])
                seconds = int(hour_minute_second[2])
                submit_time = now - timedelta(hours=hours, minutes=minutes, seconds=seconds)

        submit_time_str = str(submit_time.year) + "/" + str(submit_time.month) + "/" + str(submit_time.day) + " " + \
            str(submit_time.hour) + ":" + str(submit_time.minute) + ":" + str(submit_time.second)
        return submit_time_str

    def convert_scontrol_list_to_onelist(self, slurm_scontrol_list):
        scontrol_dict = {}
        one_list = []
        for index in slurm_scontrol_list:
            for item in index:
                one_list.append(item)
        return one_list

    def convert_scontrol_list_to_jobinfo_list(self, slurm_scontrol_list):
        job_info_list = []
        one_list = []
        num_jobs = 0
        for index in slurm_scontrol_list:
            for item in index:
                one_list.append(item)
        n = -1
        for item in one_list:
            if "JobId" in item:
                job_info_list.append({})
                n += 1
            keyvalue = item.split("=")
            job_info_list[n][keyvalue[0]] = keyvalue[1]

        return job_info_list



# ---------------------------------------------
# Helper function
def isdir(path, sftp):
    try:
        return S_ISDIR(sftp.stat(path).st_mode)
    except IOError:
        # Path does not exist, so by definition not a directory
        return False


# ---------------------------------------------
# Helper function
def clean_path(path):
    """Cleans a filepath for use on cluster by adding escape characters"""
    esc_chars = ['&', ';', '(', ')', '$', '`', '\'', ' ']
    for c in esc_chars:
        path = path.replace(c, "\\" + c)
    return path


# ---------------------------------------------
# Helper function
def reverse_clean_path(path):
    """Removes escape characters from path"""
    return path.replace("\\", "")


# ---------------------------------------------


# ---------------------------------------------
