# -*- coding: utf-8 -*-
"""
Graphical Interface for CEPAC Cluster tool Library
Name: CEPACClusterLibGui.py
Original Author: Taige Hou (thou1@partners.org)
Author: Aaron Wu (aawu@mgh.harvard.edu)
"""

import wx, threading
from wx.lib.agw import aui
import wx.lib.mixins.listctrl as listmix
import wx.lib.agw.ultimatelistctrl as ULC
from wx.lib.embeddedimage import PyEmbeddedImage
import EnhancedStatusBar
import traceback
from CEPACClusterLib import CEPACClusterApp, CLUSTER_NAMES, CLUSTER_INFO, JobInfoThread, COMMAND_LANGUAGES
from timeit import default_timer as timer

# ----------------------------------------------------------------------
MAIN_WINDOW_SIZE = (850, 650)
OUTPUT_FONT = (12, wx.FONTFAMILY_SWISS,
               wx.FONTSTYLE_NORMAL,
               wx.FONTWEIGHT_NORMAL, False)

ICON = PyEmbeddedImage(
    "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAABHNCSVQICAgIfAhkiAAAAPlJ"
    "REFUWIVjYBgFo2CkA0YC8ueoZM8vBgaGFQwMDBNI1fifBtiB3g64IS4ufpCdnR1ZTJ6eDuiC"
    "GSYuLv4QKvYLJsZEdFhQAbx48UIJymRlYGBgo7sDGBkZ//Ly8sK4GnR3AAMDA8OPHz9gzG90"
    "d0BxcbHO79+/Ydw7xOihRiJs/f//P5O/v787ExMTTGwLzAJCBdF/YlyJDzAyMjL8/49izCcG"
    "BgZ+GIfmUYBm+Uxky4nSTwW8Ap+l9EiEzxkYGD6Sq/kNA+UhIEe+2yHgGwWWt1NqOV1ACwNt"
    "ql1iMAMDAwMD1wBZvgg5FALobPlZ3BEyCkbBKKAzAABEjv0W0bs6kAAAAABJRU5ErkJggg==")
# ----------------------------------------------------------------------


########################################################################
"""Custom event to signal that output should be written"""
(OutputEvent, EVT_OUTPUT) = wx.lib.newevent.NewEvent()
"""Custom event to update the job info"""
(JobEvent, EVT_JOB) = wx.lib.newevent.NewEvent()
"""Custom event to update the progress gauge for uploads"""
(UpdateUploadEvent, EVT_UPDATE_UPLOAD) = wx.lib.newevent.NewEvent()
"""Custom event to update the progress gauge for downloads"""
(UpdateDownloadEvent, EVT_UPDATE_DOWNLOAD) = wx.lib.newevent.NewEvent()
"""Custom event to signal that a dialog should be displayed"""
(DialogEvent, EVT_DIALOG) = wx.lib.newevent.NewEvent()

# (DuplicateWarningDialogEvent, EVT_DUPLICATE_WARNING) = wx.lib.newevent.NewEvent()

########################################################################
class PanelNotebook(aui.AuiNotebook):
    """Custom class derived from AuiNotebook that handles clicks on tabs"""

    def __init__(self, *args, **kargs):
        aui.AuiNotebook.__init__(self, *args, **kargs)

    def OnTabClicked(self, event):
        aui.AuiNotebook.OnTabClicked(self, event)
        tab_index = self.GetSelection()
        panel = self.GetPage(tab_index)
        # Let the panel decide what to to when it has focus
        if hasattr(panel, "on_focus"):
            panel.on_focus(None)


########################################################################
class MainFrame(wx.Frame):
    """Main Frame class for layout of app"""

    def __init__(self):
        wx.Frame.__init__(self, None, wx.ID_ANY,
                          "CEPAC Cluster Tool",
                          size=MAIN_WINDOW_SIZE)

        self.SetIcon(ICON.GetIcon())

        # associate with CEPACClusterApp object
        self.cluster = CEPACClusterApp()

        # Set up FrameManager
        self._mgr = aui.AuiManager()
        self._mgr.SetManagedWindow(self)

        # main AuiNotebook containing all the panels
        style = aui.AUI_NB_TOP | aui.AUI_NB_TAB_SPLIT | aui.AUI_NB_TAB_MOVE | \
                aui.AUI_NB_SCROLL_BUTTONS | aui.AUI_NB_CLOSE_ON_ALL_TABS | \
                aui.AUI_NB_DRAW_DND_TAB
        self.notebook = PanelNotebook(self, -1,
                                      agwStyle=style)

        # Panels in notebook
        self.login_panel = LoginPanel(self, self.cluster)
        self.upload_panel = UploadPanel(self, self.cluster)
        self.download_panel = DownloadPanel(self, self.cluster)
        self.status_panel = StatusPanel(self, self.cluster)

        self.notebook.AddPage(self.login_panel, "login")
        self.notebook.AddPage(self.upload_panel, "upload")
        self.notebook.AddPage(self.download_panel, "download")
        self.notebook.AddPage(self.status_panel, "status")

        # Hide close buttons
        for page_num in range(self.notebook.GetPageCount()):
            self.notebook.SetCloseButton(page_num, False)

        # Text box used to print messages from Cluster App
        self.output_box = wx.TextCtrl(self, size=(-1, 300),
                                      style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP)
        self.output_box.SetFont(wx.Font(*OUTPUT_FONT))

        # Bind output box to Cluster App.
        # If called from worker thread, we post an event to the output box
        # Otherwise write directly to output box
        def gen_evt_func(text, is_thread=True):
            if is_thread:
                evt = OutputEvent(message=text)
                wx.PostEvent(self, evt)
            else:
                self.on_output(None, text)

        def gen_evt_dlg(text, is_thread=True):
            if is_thread:
                evt = DialogEvent(message=text)
                wx.PostEvent(self, evt)
            else:
                self.on_dialog(None, text)
        """
        def gen_duplicate_file_dlg(text, is_thread=True):
            if is_thread:
                evt = DuplicateWarningDialogEvent(message=text)
                wx.PostEvent(self, evt)
                return None
            else:
                selection = self.on_duplicate_file_dialog(None, text)
                return selection
        """

        # print_func = lambda text:self.output_box.AppendText(text+"\n")
        self.cluster.bind_output(gen_evt_func)
        self.cluster.bind_dialog(gen_evt_dlg)
        # self.cluster.bind_duplicate_warning_dialog(gen_duplicate_file_dlg)

        self._mgr.AddPane(self.notebook, aui.AuiPaneInfo().Name("notebook_content").CenterPane().CloseButton(False))
        self._mgr.AddPane(self.output_box, aui.AuiPaneInfo().Name("output").
                          Bottom().CloseButton(False).CaptionVisible(False).
                          BestSize(-1, 230))

        # status bar
        # self.setup_statusbar()

        # commit changes
        self._mgr.Update()

        self.Bind(EVT_OUTPUT, self.on_output)
        self.Bind(EVT_DIALOG, self.on_dialog)
        # self.Bind(EVT_DUPLICATE_WARNING, self.on_duplicate_file_dialog)


    # function that sets up status bar. Not used on current version of cluster tool (gauge in upload tab)
    """
    def setup_statusbar(self):
        self.statusbar = EnhancedStatusBar.EnhancedStatusBar(self)
        self.statusbar.GetParent().SendSizeEvent()
        self.statusbar.SetFieldsCount(3)
        self.statusbar.SetStatusWidths([55,150,40])
        # self.upload_gauge = wx.Gauge(self.statusbar, -1, range = 100, size = (200,-1))
        #Add progress gauge to upload panel
        # self.upload_panel.add_gauge(self.upload_gauge)
        self.statusbar.SetFont(wx.Font(9,wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_NORMAL))
        #self.abort_upload_btn = wx.Button(self.statusbar, -1, "Abort", size=(50,-1))
        self.statusbar.AddWidget(wx.StaticText(self.statusbar, -1, "Upload"), pos = 0)
        # self.statusbar.AddWidget(self.upload_gauge, pos = 1)
        # self.statusbar.AddWidget(self.abort_upload_btn)
        self.SetStatusBar(self.statusbar)

        # self.Bind(wx.EVT_BUTTON, self.on_abort_upload, self.abort_upload_btn)
    """

    def on_output(self, event, text=""):
        """Called to print text to the output box"""
        if event:
            self.output_box.AppendText(event.message + "\n")
        else:
            self.output_box.AppendText(text + "\n")

    def on_dialog(self, event, text=""):
        """Called to display an error dialog with message"""
        if event:
            error_message(self, event.message)

    """
    def on_duplicate_file_dialog(self, event, text=""):
        if event:
            selection = duplicate_file_message(self, event.message)
            return selection
    """

    def on_abort_upload(self, event):
        """ Called to abort uploading of run files. Stops the upload thread."""
        if self.cluster.upload_thread:
            self.cluster.upload_thread.stop()
            self.output_box.AppendText("\tUpload Stopped\n")


########################################################################
class LoginPanel(wx.Panel):
    """Panel that handles login to the cluster"""

    def __init__(self, parent, cluster):
        wx.Panel.__init__(self, parent)
        self.cluster = cluster
        self.parent = parent
        # List of clusters to choose from.  Custom allows user to manually input hostname
        self.cluster_cb = wx.ComboBox(self, -1, value=CLUSTER_NAMES[0],
                                      choices=list(CLUSTER_NAMES),
                                      style=wx.CB_READONLY)
        self.hostname_tc = wx.TextCtrl(self, -1, CLUSTER_INFO[CLUSTER_NAMES[0]]['host'], size=(200, -1))
        self.runfolder_tc = wx.TextCtrl(self, -1, CLUSTER_INFO[CLUSTER_NAMES[0]]['run_folder'], size=(170, -1))
        self.modelfolder_tc = wx.TextCtrl(self, -1, CLUSTER_INFO[CLUSTER_NAMES[0]]['model_folder'], size=(200, -1))
        self.username_tc = wx.TextCtrl(self, -1, )
        self.password_tc = wx.TextCtrl(self, -1, style=wx.TE_PASSWORD)
        login_btn = wx.Button(self, 10, "Login")
        self.scheduler = wx.StaticText(self, -1, "Scheduler:")
        self.scheduler_cb = wx.ComboBox(self, -1, value = COMMAND_LANGUAGES[0], choices=list(COMMAND_LANGUAGES),
                                style=wx.CB_READONLY)
        # Sets the default cluster information on init
        self.on_change_host(None)

        # Layout
        gbs = wx.GridBagSizer(10, 20)
        gbs.Add(wx.StaticText(self, -1, "hostname"), (0, 2))
        gbs.Add(wx.StaticText(self, -1, "run folder"), (0, 3))
        gbs.Add(wx.StaticText(self, -1, "model folder"), (0, 4))
        gbs.Add(wx.StaticText(self, -1, "Cluster:"), (1, 0))
        gbs.Add(self.cluster_cb, (1, 1))
        gbs.Add(self.hostname_tc, (1, 2))
        gbs.Add(self.runfolder_tc, (1, 3))
        gbs.Add(self.modelfolder_tc, (1, 4))
        gbs.Add(wx.StaticText(self, -1, "Username:"), (3, 0))
        gbs.Add(self.username_tc, (3, 1))
        gbs.Add(wx.StaticText(self, -1, "Password:"), (4, 0))
        gbs.Add(self.password_tc, (4, 1))
        gbs.Add(login_btn, (5, 2))
        gbs.Add(self.scheduler, (2,0))
        gbs.Add(self.scheduler_cb, (2, 1))

        self.Bind(wx.EVT_COMBOBOX, self.on_change_host, self.cluster_cb)
        self.Bind(wx.EVT_BUTTON, self.on_login, login_btn)
        self.password_tc.Bind(wx.EVT_KEY_UP, self.on_keypress)
        self.SetSizer(gbs)

    def on_change_host(self, event):
        """Changes the displayed cluster information depending on which host is selected"""
        cluster_name = self.cluster_cb.GetValue()
        self.hostname_tc.SetValue(CLUSTER_INFO[cluster_name]['host'])
        self.runfolder_tc.SetValue(CLUSTER_INFO[cluster_name]['run_folder'])
        self.modelfolder_tc.SetValue(CLUSTER_INFO[cluster_name]['model_folder'])

    def on_login(self, event):
        """
        Calls the ClusterApp connect function
        """

        hostname = self.hostname_tc.GetValue()
        username = self.username_tc.GetValue()
        password = self.password_tc.GetValue()
        run_path = self.runfolder_tc.GetValue()
        model_path = self.modelfolder_tc.GetValue()
        clustername = self.cluster_cb.GetValue()
        scheduler = self.scheduler_cb.GetValue()

        if clustername == "ERISOne" and scheduler == "SLURM":
            error_message(self, "ERISOne cannot use SLURM scheduler. Please use the LSF scheduler for ERISOne.")
        else:
            self.cluster.connect(hostname, username, password, run_path, model_path, clustername, scheduler)

            # refill fields on other tabs with new cluster information
            self.parent.upload_panel.refill_fields()
            # reset the download panel
            self.parent.download_panel.clear_all()
            # reset the status panel
            self.parent.status_panel.clear_all()
            # create the thread that checks if SSH connection is established
            self.cluster.create_connection_thread()

    def on_keypress(self, event):
        """Binds enter key to login button"""
        keycode = event.GetKeyCode()
        if keycode == wx.WXK_RETURN:
            self.on_login(None)
        event.Skip()

# #######################################################################
class UploadPanel(wx.Panel):
    """Panel that handles creation of jobs and uploading folders to the cluster"""

    def __init__(self, parent, cluster):
        wx.Panel.__init__(self, parent)
        self.cluster = cluster

        self.model_type_cb = wx.ComboBox(self, -1, style=wx.CB_READONLY)
        self.model_version_cb = wx.ComboBox(self, -1, size=(300, -1), style=wx.CB_READONLY)
        self.queue_cb = wx.ComboBox(self, -1, style=wx.CB_READONLY)
        self.custom_queue_cb = wx.CheckBox(self, -1, style=0, label="Custom Queue")
        self.custom_queue_tc = wx.TextCtrl(self, -1, size=(170, -1))
        self.custom_queue_tc.AppendText("N/A")  # custom queue text box has "N/A" entered as default
        self.email_tc = wx.TextCtrl(self, -1, size=(200, -1))
        self.jobname_tc = wx.TextCtrl(self, -1, size=(170, -1))
        self.local_dir_tc = wx.TextCtrl(self, -1, size=(450, -1))
        self.custom_command_cb = wx.CheckBox(self, -1, style = 0, label = "Custom Command")
        self.custom_command_tc = wx.TextCtrl(self, size = (400, 50), style = wx.TE_MULTILINE)
        self.custom_command_tc.AppendText("N/A")
        browse_btn = wx.Button(self, 20, "...")
        upload_btn = wx.Button(self, 10, "Submit")
        self.upload_gauge = wx.Gauge(self, -1, range=100, size=(200, -1))
        self.abort_upload_btn = wx.Button(self, -1, "Abort", size=(50, -1))

        # Layout
        gbs = wx.GridBagSizer(10, 20)
        gbs.Add(wx.StaticText(self, -1, "Model Type"), (0, 0))
        gbs.Add(wx.StaticText(self, -1, "Model Version"), (1, 0))
        gbs.Add(wx.StaticText(self, -1, "Queue"), (2, 0))
        gbs.Add(wx.StaticText(self, -1, "Email"), (4, 0))
        gbs.Add(wx.StaticText(self, -1, "Job Name"), (5, 0))
        gbs.Add(wx.StaticText(self, -1, "Input Directory"), (6, 0))

        gbs.Add(self.model_type_cb, (0, 1))
        gbs.Add(self.model_version_cb, (1, 1))
        gbs.Add(self.queue_cb, (2, 1))
        gbs.Add(self.custom_queue_cb, (3, 0))
        gbs.Add(self.custom_queue_tc, (3, 1))
        gbs.Add(self.email_tc, (4, 1))
        gbs.Add(self.jobname_tc, (5, 1))
        gbs.Add(self.local_dir_tc, (6, 1))
        gbs.Add(browse_btn, (6, 2))

        gbs.Add(self.custom_command_cb, (7, 0))
        gbs.Add(self.custom_command_tc, (7, 1))

        gbs.Add(upload_btn, (8, 0))

        gbs.Add(wx.StaticText(self, -1, "Upload Progress:"), (9, 0))
        gbs.Add(self.upload_gauge, (10, 0))
        # self.add_gauge(upload_gauge)
        gbs.Add(self.abort_upload_btn, (10, 1))

        self.Bind(wx.EVT_COMBOBOX, self.on_select_model_type, self.model_type_cb)
        self.Bind(wx.EVT_CHECKBOX, self.on_custom_queue_checkbox_clicked, self.custom_queue_cb)
        self.Bind(wx.EVT_BUTTON, self.on_browse, browse_btn)
        self.Bind(wx.EVT_BUTTON, self.on_upload, upload_btn)
        self.Bind(wx.EVT_BUTTON, self.on_abort_upload, self.abort_upload_btn)
        self.Bind(EVT_UPDATE_UPLOAD, self.on_update_progress)
        self.Bind(wx.EVT_CHECKBOX, self.on_custom_command_checkbox_clicked, self.custom_command_cb)
        self.SetSizer(gbs)

    def add_gauge(self, progress_gauge):
        """associates a gauge widget with this panel"""
        self.progress_gauge = progress_gauge

    def refill_fields(self):
        """Fills the model types combo box using the names of the relevant executables on the cluster"""
        if self.cluster.model_versions:
            model_types = list(self.cluster.model_versions.keys())
            self.model_type_cb.Set(model_types)

            default_value = model_types[0]
            if "treatm" in model_types:
                default_value = "treatm"

            self.model_type_cb.SetStringSelection(default_value)

            # Fill model versions
            self.on_select_model_type(None)
        # Fill the queues combo box
        if self.cluster.queues:
            self.queue_cb.Set(self.cluster.queues)
            self.queue_cb.SetStringSelection(self.cluster.queues[0])

    def on_select_model_type(self, event):
        """
        Called when a model type is selected
        Fills in model versions for that model type
        """
        model_type = self.model_type_cb.GetValue()
        versions = self.cluster.model_versions[model_type]
        self.model_version_cb.Set(versions)

        if versions:
            self.model_version_cb.SetStringSelection(versions[-1])

    def on_custom_queue_checkbox_clicked(self, event):
        """
        Called when the custom queue checkbox is clicked.
        Disables the Queue drop-down and allows users to fill in the custom queue text box
        """
        if self.custom_queue_cb.GetValue():
            self.queue_cb.Clear()
            self.custom_queue_tc.Clear()
        elif not self.custom_queue_cb.GetValue():
            self.queue_cb.Set(self.cluster.queues)
            self.queue_cb.SetStringSelection(self.cluster.queues[0])
            self.custom_queue_tc.Clear()
            self.custom_queue_tc.AppendText("N/A")

    def on_custom_command_checkbox_clicked(self, event):
        if self.custom_command_cb.GetValue():
            self.custom_command_tc.Clear()

        elif not self.custom_command_cb.GetValue():
            self.custom_command_tc.Clear()
            self.custom_command_tc.AppendText("N/A")


    def on_upload(self, event):
        """Calls cluster app to upload folders and submit jobs"""
        dir_local = self.local_dir_tc.GetValue()
        dir_remote = self.cluster.run_path
        queue_used = self.queue_cb.GetValue()  # queue used will be the selected item under "Queue"...
        if self.custom_queue_cb.GetValue():  # ... unless the custom queue checkbox was checked
            queue_used = self.custom_queue_tc.GetValue()
        lsfinfo = {'jobname': self.jobname_tc.GetValue(),
                   'queue': queue_used,
                   'modeltype': self.model_type_cb.GetValue(),
                   'modelversion': self.model_version_cb.GetValue()}

        if self.email_tc.GetValue():
            lsfinfo['email'] = self.email_tc.GetValue()

        if self.custom_command_cb.GetValue():  # if custom command checkbox checked
            lsfinfo['customcommand'] = self.custom_command_tc.GetValue()

        def update_func(progress):
            evt = UpdateUploadEvent(progress=progress)
            wx.PostEvent(self, evt)

        pattern = ("*.in",)
        if lsfinfo['modeltype'] == "smoking":
            pattern = ("*.smin", "*.xlsm")
        elif lsfinfo['modeltype'] == "covid-19":
            pattern = ("*.json",)

        if self.cluster.duplicate_folder(dir_local, dir_remote):
            dlg = wx.MessageDialog(self, "Folder of the same name is already in the cluster's run folder. \n"
                                         "Contents within the folders of the same name will be overwritten and "
                                         "contents with new names will be added to the existing folder. \n"
                                         "This will not affect the uploaded runs but may result in extra runs being computed.\n"
                                         "To avoid this, delete the existing folder or subfolders in the DOWNLOADS tab.\n"
                                         "Otherwise, press OK to continue upload or CANCEL to stop upload.",
                                   "Duplicate folder found",
                                   wx.OK | wx.CANCEL)
            selection = dlg.ShowModal()
            dlg.Destroy()
            if selection == wx.ID_OK:
                pass
            elif selection == wx.ID_CANCEL:
                return

        self.cluster.create_upload_thread(dir_local, dir_remote,
                                            lsfinfo, update_func, pattern)
        self.upload_gauge.SetValue(0)  # may not be necessary
        # jobfiles = self.cluster.sftp_upload(dir_local, dir_remote, lsfinfo, update_func, pattern)

        # submit jobs
        # self.cluster.pybsub(jobfiles)

    def on_browse(self, event):
        """Handles browsing for local dir. Opens file explore dialog and gets path of directory chosen"""
        dlg = wx.DirDialog(self, "Choose a directory:")
        if dlg.ShowModal() == wx.ID_OK:
            self.local_dir_tc.SetValue(dlg.GetPath())
        dlg.Destroy()

    def on_update_progress(self, event):
        """Updates progress bar for uploads"""
        self.upload_gauge.SetValue(int(event.progress))

    def on_abort_upload(self, event):
        """Activates the function in te upload thread to abort the upload and output the "upload stopped" message"""
        if self.cluster.upload_thread:
            self.cluster.upload_thread.stop()
            self.output_box.AppendText("\tUpload Stopped\n")


########################################################################
class DownloadPanel(wx.Panel):
    """Panel that handles downloading of folders from the cluster"""

    def __init__(self, parent, cluster):
        wx.Panel.__init__(self, parent)
        self.cluster = cluster

        # This is required to fix a bug with GenericDirCtrl in this version of wx
        self.local = wx.Locale(wx.LANGUAGE_ENGLISH)

        # dict of progress gauges mapping folder names to gauges
        self.gauges = {}

        # List Control of base run folder on cluster
        self.remote_browser = ULC.UltimateListCtrl(self, -1, size=(-1, 275),
                                                   agwStyle=wx.LC_REPORT | wx.LC_VRULES | wx.LC_HRULES
                                                            | wx.LC_SINGLE_SEL | ULC.ULC_AUTO_CHECK_CHILD)
        self.refresh_remote_btn = wx.Button(self, 20, "Refresh")
        self.download_btn = wx.Button(self, 30, "Download")
        self.delete_btn = wx.Button(self, 40, "Delete")

        # Layout
        flex = wx.FlexGridSizer(cols=1, vgap=0, hgap=0)
        flex.Add(self.remote_browser, 0, wx.EXPAND)
        flex.Add(self.refresh_remote_btn, 0)

        flex.Add(self.download_btn, 0)
        flex.Add(self.delete_btn, 0, wx.TOP, 10)

        flex.AddGrowableCol(0)

        self.Bind(wx.EVT_BUTTON, self.on_refresh, self.refresh_remote_btn)
        self.Bind(wx.EVT_BUTTON, self.on_download, self.download_btn)
        self.Bind(wx.EVT_BUTTON, self.on_delete, self.delete_btn)
        self.Bind(EVT_UPDATE_DOWNLOAD, self.on_update_download)
        self.SetSizer(flex)

    def on_show_subfolders(self, event):
        folders_to_expand = []
        for row_index in range(self.remote_browser.GetItemCount()):
            if self.remote_browser.GetItem(row_index, 0).IsChecked():
                remote_path = self.remote_browser.GetItem(row_index, 1).GetText()
                folders_to_expand.append(remote_path)
        for folder in folders_to_expand:
            run_folder_path = self.cluster.run_path + "/" + folder
            self.cluster.get_run_subfolders(run_folder_path)


    def on_refresh(self, event):
        """Refresh the list of Run folders on the cluster"""
        self.remote_browser.ClearAll()

        # Add Column Headers
        info = ULC.UltimateListItem()
        info._mask = ULC.ULC_MASK_CHECK
        info._kind = 1
        info._footerFont = None
        self.remote_browser.InsertColumnInfo(0, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Run Folder"
        self.remote_browser.InsertColumnInfo(1, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Date Modified"
        self.remote_browser.InsertColumnInfo(2, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Progress"
        self.remote_browser.InsertColumnInfo(3, info)

        info = ULC.UltimateListItem()
        info._mask = ULC.ULC_MASK_CHECK
        info._kind = 1
        info._footerFont = None
        self.remote_browser.InsertColumnInfo(4, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Subfolder"
        self.remote_browser.InsertColumnInfo(5, info)

        # Add data
        try:
            for index, entry in enumerate(self.cluster.get_run_folders()):
                run_folder = entry  # the individual run folder, one at a time per loop
                # checkbox
                self.remote_browser.InsertStringItem(index, "", it_kind=1)

                # Directory name
                self.remote_browser.SetStringItem(index, 1, run_folder[0])
                # Directory Modified Date
                self.remote_browser.SetStringItem(index, 2, run_folder[1])
                # Download Progress Gauges
                self.remote_browser.SetStringItem(index, 3, "")
                self.gauges[run_folder[0]] = wx.Gauge(self.remote_browser, -1, 50, style=wx.GA_HORIZONTAL | wx.GA_SMOOTH)
                item = self.remote_browser.GetItem(index, 3)
                item.SetWindow(self.gauges[run_folder[0]], (index, 3))
                self.remote_browser.SetItem(item)

        except Exception as error:
            with open('logfile.txt', 'w') as log:
                log.write(str(traceback.format_exc()))
                log.write("\nLogin may have been invalid. \n")
            error_message(self, str(error) + '. \nPlease try logging in again')

        # item = self.remote_browser.GetItem(1,1) #commented
        # item.SetWindow(self.gauges) #commented
        # self.remote_browser.SetItem(item) #commented
        self.remote_browser.SetColumnWidth(0, wx.LIST_AUTOSIZE)
        self.remote_browser.SetColumnWidth(1, wx.LIST_AUTOSIZE)
        self.remote_browser.SetColumnWidth(2, wx.LIST_AUTOSIZE)
        self.remote_browser.SetColumnWidth(3, wx.LIST_AUTOSIZE)



    def on_update_download(self, event):
        """Handles updates to progress bars for downloads"""
        run_folder = event.run_folder
        if run_folder in self.gauges:
            self.gauges[run_folder].SetValue(int(event.progress))

    def on_download(self, event):
        """Recursively Downloads the directories selected by user"""
        # Get paths of checked items
        items_to_download = []
        for row_index in range(self.remote_browser.GetItemCount()):
            if self.remote_browser.GetItem(row_index, 0).IsChecked():
                remote_path = self.remote_browser.GetItem(row_index, 1).GetText()
                items_to_download.append(remote_path)

        # Create Dir Dialog to pick local dir
        dir_local = None
        if items_to_download:
            dlg = wx.DirDialog(self, "Choose a folder:")
            if dlg.ShowModal() == wx.ID_OK:
                dir_local = dlg.GetPath()
            dlg.Destroy()

        def update_func(progress, run_folder):
            evt = UpdateDownloadEvent(progress=progress, run_folder=run_folder)
            wx.PostEvent(self, evt)

        # Download selected folders
        if dir_local:
            for run_folder in items_to_download:
                dir_remote = self.cluster.run_path + "/" + run_folder
                self.cluster.create_download_thread(run_folder, dir_remote, dir_local, update_func)

    def on_delete(self, event):
        """Deletes the directories selected by user"""
        # Get paths of checked items
        items_to_delete = []
        indices_to_delete = []
        for row_index in range(self.remote_browser.GetItemCount()):
            if self.remote_browser.GetItem(row_index, 0).IsChecked():
                remote_path = self.remote_browser.GetItem(row_index, 1).GetText()
                items_to_delete.append(remote_path)
                indices_to_delete.append(row_index)

        # Confirm Delete
        if items_to_delete:
            dlg = wx.MessageDialog(self, "Deleting:\n" + "\n".join(items_to_delete),
                                   "Deleting Folders",
                                   wx.OK | wx.CANCEL)
            if dlg.ShowModal() == wx.ID_OK:
                self.cluster.delete_run_folders(items_to_delete)
                # reverse sort the indices so we don't run into trouble while deleting from for loop
                indices_to_delete.reverse()
                for index in indices_to_delete:
                    self.remote_browser.DeleteItem(index)
            dlg.Destroy()

    def clear_all(self):
        self.remote_browser.ClearAll()

########################################################################
class StatusPanel(wx.Panel):
    """Panel that displays status of currently running jobs on the cluster"""

    def __init__(self, parent, cluster):
        wx.Panel.__init__(self, parent)
        self.cluster = cluster

        # List Control of base run folder on cluster
        self.job_browser = ULC.UltimateListCtrl(self, -1, size=(-1, 300),
                                                agwStyle=wx.LC_REPORT | wx.LC_VRULES | wx.LC_HRULES
                                                         | wx.LC_SINGLE_SEL | ULC.ULC_AUTO_CHECK_CHILD)
        self.refresh_btn = wx.Button(self, 10, "Refresh")
        self.kill_btn = wx.Button(self, 30, "Kill")
        # Layout
        flex = wx.FlexGridSizer(cols=1)
        flex.Add(self.job_browser, 0, wx.EXPAND)
        flex.Add(self.refresh_btn, 0)
        flex.Add(self.kill_btn, 0)
        flex.AddGrowableCol(0)

        # Binding events to buttons on the tab
        self.refresh_btn.Bind(wx.EVT_BUTTON, self.on_refresh)
        self.kill_btn.Bind(wx.EVT_BUTTON, self.on_just_kill)
        self.Bind(EVT_JOB, self.on_job)

        self.SetSizer(flex)

    def on_refresh(self, event):
        """Refresh the list of jobs"""
        self.cluster.output_get_job_list()
        self.job_browser.ClearAll()
        # Add Column Headers
        info = ULC.UltimateListItem()
        info._mask = ULC.ULC_MASK_CHECK
        info._kind = 1
        info._footerFont = None
        self.job_browser.InsertColumnInfo(0, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "ID"
        self.job_browser.InsertColumnInfo(1, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Status"
        self.job_browser.InsertColumnInfo(2, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Queue"
        self.job_browser.InsertColumnInfo(3, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Job Name"
        self.job_browser.InsertColumnInfo(4, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Model"
        self.job_browser.InsertColumnInfo(5, info)
        jobids = []

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Folder"
        self.job_browser.InsertColumnInfo(6, info)

        info = ULC.UltimateListItem()
        info._format = wx.LIST_FORMAT_RIGHT
        info._mask = wx.LIST_MASK_TEXT
        info._text = "Time of Submission"
        self.job_browser.InsertColumnInfo(7, info)

        # Add basic data5
        try:
            numjobs = 0  # count of number of jobs found
            for index, job_data in enumerate(self.cluster.get_job_list()):
                numjobs += 1
                if self.cluster.scheduler == "LSF":
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
                    # checkbox
                    self.job_browser.InsertStringItem(index, "", it_kind=1)
                    self.job_browser.SetStringItem(index, 1, jobid)
                    self.job_browser.SetStringItem(index, 2, status)
                    self.job_browser.SetStringItem(index, 3, queue)
                    self.job_browser.SetStringItem(index, 7, full_submit_time)
                elif self.cluster.scheduler == "SLURM":  # if scheduler is SLURM.
                    jobid, status, queue = job_data
                    jobids.append(jobid)
                    self.job_browser.InsertStringItem(index, "", it_kind=1)
                    self.job_browser.SetStringItem(index, 1, jobid)
                    self.job_browser.SetStringItem(index, 2, status)
                    self.job_browser.SetStringItem(index, 3, queue)

                    """
                    jobid, status, queue, name, folder, submit_time = job_data

                    self.job_browser.InsertStringItem(index, "", it_kind=1)
                    self.job_browser.SetStringItem(index, 1, jobid)
                    self.job_browser.SetStringItem(index, 2, status)
                    self.job_browser.SetStringItem(index, 3, queue)
                    self.job_browser.SetStringItem(index, 4, name)
                    self.job_browser.SetStringItem(index, 6, folder)
                    self.job_browser.SetStringItem(index, 7, submit_time)
                    """
            if numjobs == 0:
                self.cluster.output("\nNo running jobs found")
        except Exception as error:
            with open('logfile.txt', 'w') as log:
                log.write(str(traceback.format_exc()))
                log.write("\nLogin may have been invalid. \n")
            error_message(self, str(error) + '. \nPlease try logging in again')

        def job_evt_func(jobid, data):
            """Function to be passed to the job thread. """
            evt = JobEvent(jobid=jobid, data=data)
            wx.PostEvent(self, evt)

        # Add detailed data
        for index, jobid in enumerate(jobids):
            # create Job thread
            job_thread = JobInfoThread(self.cluster, jobid, job_evt_func)
            job_thread.daemon = True
            job_thread.start()
            import time
            time.sleep(.01)

    def on_job(self, event):
        """Updates display with detailed job info"""
        jobid = event.jobid
        job_info = event.data

        if job_info:
            if self.cluster.scheduler == "LSF":
                for index in range(self.job_browser.GetItemCount()):
                    if str(self.job_browser.GetItem(index, 1).GetText()) == str(jobid):
                        job_name, model_version, run_folder = job_info
                        self.job_browser.SetStringItem(index, 4, job_name)
                        self.job_browser.SetStringItem(index, 5, model_version)
                        self.job_browser.SetStringItem(index, 6, run_folder)
                        # self.job_browser.SetStringItem(index, 7, submission_time)
            elif self.cluster.scheduler == "SLURM":
                for index in range(self.job_browser.GetItemCount()):
                    if str(self.job_browser.GetItem(index, 1).GetText()) == str(jobid):
                        job_name, model_version, run_folder, submit_time = job_info
                        self.job_browser.SetStringItem(index, 4, job_name)
                        self.job_browser.SetStringItem(index, 5, model_version)
                        self.job_browser.SetStringItem(index, 6, run_folder)
                        self.job_browser.SetStringItem(index, 7, submit_time)

        for i in range(self.job_browser.GetColumnCount()):
            self.job_browser.SetColumnWidth(i, wx.LIST_AUTOSIZE)

    def on_just_kill(self, event):
        """Deletes the directories selected by user"""
        # Get paths of checked items
        jobs_to_kill = []
        job_indices = []
        for row_index in range(self.job_browser.GetItemCount()):
            if self.job_browser.GetItem(row_index, 0).IsChecked():
                jobid = self.job_browser.GetItem(row_index, 1).GetText()
                jobfilename = self.job_browser.GetItem(row_index, 6).GetText()
                jobs_to_kill.append(jobid)
                job_indices.append(row_index)

        # Confirm Delete
        if jobs_to_kill:
            dlg = wx.MessageDialog(self, "Kill Selected Jobs?",
                                   "Kill Jobs",
                                   wx.OK | wx.CANCEL)
            if dlg.ShowModal() == wx.ID_OK:
                self.cluster.kill_jobs(jobs_to_kill)
                # reverse sort the indices, so we don't run into trouble while deleting from for loop
                job_indices.reverse()
            dlg.Destroy()

    def clear_all(self):
        self.job_browser.ClearAll()

def error_message(parent, message):
    # shows error message
    dlg = wx.MessageDialog(parent, message,
                           'Error',
                           wx.OK)
    dlg.ShowModal()
    dlg.Destroy()

"""
def duplicate_file_message(parent, message):
    # shows duplicate file warning
    dlg = wx.MessageDialog(parent, message,
                           'Duplicate file found.',
                           wx.OK|wx.CANCEL)
    selection = dlg.ShowModal()
    dlg.Destroy()
    if selection == wx.ID_OK:
        return True
    else:
        return False
"""

if __name__ == "__main__":
    # Run the program
    app = wx.App()
    frame = MainFrame()
    frame.Show()
    app.MainLoop()
