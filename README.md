# build-ilastik-on-windows
Build Ilastik and its dependencies using setuptools.

You should have CMake installed. On Windows, you should run this script within
the Microsoft SDK for Windows command-line shell or the Microsoft Visual C++
For Python 2.7 command-line shell.

At this point, this is an experimental attempt to create a setuptools setup.py file for building Ilastik 0.5 and Vigra. The goal is, given a Windows computer with Python, Visual C++ for Python 2.7 and CMake installed, to build a functional Ilastik package that can be used stand-alone or in conjunction with CellProfiler's ClassifyPixels module.

Most likely, this repo's ownership will be transferred to CellProfiler - please do not take it seriously yet.

Tested as follows:

    > pip freeze
    cycler==0.9.0
    Cython==0.23.4
    h5py==2.5.0
    lxml==3.4.4
    matplotlib==1.5.0
    MySQL-python==1.2.5
    numpy==1.9.3
    PyOpenGL==3.1.1b1
    pyparsing==2.0.6
    PyQt4==4.11.4
    python-dateutil==2.4.2
    pytz==2015.7
    pyzmq==15.1.0
    qimage2ndarray==1.3.1
    scipy==0.16.1
    six==1.10.0
    wxPython==3.0.2.0
    wxPython-common==3.0.2.0

Open Visual C++ for Python 2.7 x64 command prompt
python setup.py build
