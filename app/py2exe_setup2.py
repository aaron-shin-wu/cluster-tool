from distutils.core import setup
import py2exe

import sys; sys.argv.append('py2exe')
sys.setrecursionlimit(5000)
import paramiko
import ecdsa

includes = []#["zmq.backend.cython"]
excludes = ['scipy', 'tcl', 'Tkconstants', 'Tkinter',"zmq.libzmq",'_gtkagg', '_tkagg']
packages = ['paramiko', 'ecdsa']
dll_excludes = ['libgdk-win32-2.0-0.dll', 'libgobject-2.0-0.dll', 'hdf5.dll','libzmq.pyd']


setup(options = {
    "py2exe":
        {	
			"compressed": 2,
			"optimize": 2,
			"includes": includes,
                 "excludes": excludes, 
			"packages": packages,
                 "dll_excludes": dll_excludes,
			"bundle_files": 1,
			"dist_dir": "dist",
			"skip_archive": False,
			"compressed": True
        }
    },
	#data_files = matplotlib.get_py2exe_datafiles(),
	zipfile = None,
	windows = [{
		"script": 'CEPACClusterLibGui.py',
		"icon_resources": [(1, "coffee.ico")]
		}
		]
    )
