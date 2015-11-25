import setuptools
import distutils.command.build
from distutils.errors import DistutilsSetupError
import distutils.sysconfig
import distutils.spawn
import hashlib
import os
import re
import requests
import shutil
import StringIO
import sys
import tarfile
import tempfile
import urllib2
import urlparse
import zipfile

#
# Things that need to be built
#
# Ilastik
#     vigra-numpy
#          libhdf5 - so that CMake has an installation of it
#              zlib
#              szip
#          boost
#
# Things that need to be installed
# QT
#

is_win = sys.platform.startswith('win')
if is_win:
    from distutils.msvc9compiler import get_build_version
    lib_ext = "lib"
    dll_ext = "dll"
    build_version = get_build_version()
    toolset = "vc%d" % (int(build_version) * 10)
else:
    lib_ext = "so"
    dll_ext = "so"
    toolset = None
    
class BuildWithCMake(setuptools.Command):
    user_options = [ 
        ("cmake", None, "Location of CMake executables"),
        ("install-dir", None, "Package install directory")
    ]
    
    def initialize_options(self):
        self.build_lib = None
        self.cmake = None
        self.source_dir = None
        self.target_dir = None
        self.src_command = None
        self.extra_cmake_options = []
        self.install_dir = None
        self.install_root = None
        self.do_install = True
        
    def finalize_options(self):
        self.set_undefined_options(
            'build', ('build_lib', 'build_lib'))
        self.set_undefined_options('build', ('cmake', 'cmake'))
        if self.cmake is None and is_win:
            path = r"C:\Program Files (x86)\CMake\bin"
            if os.path.exists(path):
                self.cmake = os.path.join(path, "cmake")
	    else:
	        for path in os.environ["PATH"].split(";"):
		    cmake_path = os.path.join(path, "cmake.exe")
		    if os.path.exists(cmake_path):
		        self.cmake = cmake_path
			break
		else:
                    raise distutils.command.build.DistutilsOptionError(
                        "CMake is not installed in the default location and --cmake not specified")
        elif self.cmake is None:
            self.cmake = "cmake"
        if self.source_dir is None:
            self.set_undefined_options(
                self.src_command, ("source_dir", "source_dir"))
        root, leaf = os.path.split(self.source_dir)
        if self.target_dir is None:
            self.target_dir = os.path.join(root, "tmp", leaf)
        if self.install_root is None:
            self.install_root = os.path.abspath(
                os.path.join(root, "install", leaf))
        if self.install_dir is None:
            if is_win:
                self.install_dir = self.install_root
            else:
                self.install_dir = os.path.join(
                    self.install_root, "usr", "local")
    
    def get_sub_commands(self):
        if os.path.exists(self.source_dir):
            return []
        return [self.src_command]
    
    def get_cmake_generator(self):
        if is_win:
            return "NMake Makefiles"
        else:
            return "Unix Makefiles"
        
    def get_make_program(self):
        if is_win:
            return "nmake"
        return "make"
        
    def run(self):
        cmake_args = [self.cmake]
        cmake_args += ["-G", self.get_cmake_generator()]
        if self.do_install and is_win:
            cmake_args.append(
                '"-DCMAKE_INSTALL_PREFIX:PATH=%s"' % 
                os.path.abspath(self.install_root))
        target_dir = os.path.abspath(self.target_dir)
        if is_win:
            cmake_args.append('-DCMAKE_BUILD_TYPE:STRING="Release"')
        cmake_args += self.extra_cmake_options
        if not os.path.exists(self.target_dir):
            os.makedirs(self.target_dir)
        # I don't like changing directories. I can't see any way to make
        # cmake build its makefiles in another directory
        old_dir = os.path.abspath(os.curdir)
        source_dir = os.path.abspath(self.source_dir)
        cmake_args.append(source_dir)
        os.chdir(target_dir)
        try:
            try:
                self.spawn(cmake_args)
            except SystemExit:
                logfile = os.path.join("CMakeFiles", "CMakeError.log")
                with open(logfile, "r") as fd:
                    for line in fd:
                        self.announce(line)
                raise
            os.chdir(target_dir)
            self.spawn([self.get_make_program()])
            if self.do_install:
                if is_win:
                    self.spawn([self.get_make_program(), "install"])
                else:
                    self.spawn([self.get_make_program(),
                                "DESTDIR=%s" % os.path.abspath(self.install_root),
                                "install"])
        finally:
            os.chdir(old_dir)

class FetchSource(setuptools.Command, object):
    '''Download and untar a tarball or zipfile
    
    interesting configurable attributes:
    
    package_name - the name of the package, used to provide defaults for
                   other stuff. Defaults to 
                   self.get_command_name().rpartition("_")[-1] (e.g.
                   "fetch_foo" has a default package name of "foo")
    version - the version of the package to be fetched.
    full_name - the full name of the source, defaults to
                "{package_name}-{version}"
    url - the download source. FetchSource untars based on the extension.
          The url is parameterizable using .format(d) where d is a dictionary
          containing the package name, full name and version. For instance,
          "http://my.org/package-{version}.tar.gz" will be parameterizable
          by the version attribute. The default URL assumes that the
          package name is both the owner and repo name of a Github repo
          and that the version is tagged.
    unpack_dir - where to unpack the tarball, relative to the build library
                 directory. Defaults to package name
    source_dir - where the source unpacks to. Defaults to fullname. This should
                 match the structure of the tarball itself - we do not infer.
    post_fetch - a callable object to be run after the source has been downloaded
                 and untarred, e.g. to apply a patch. Called with the command
                 as the single argument
    member_filter - a function that evaluates a path in the tarball and returns
                    True only if the associated member should be untarred.
    '''
    user_options = [
        ( 'package-name', None, 'Name of the package being fetched' ),
        ( 'github-owner', None, 'Name of the Github owner organization for the repo'),
        ( 'full-name', None, "Package name + version" ),
        ( 'version' , None, 'Revision # of the package' ),
        ( 'url', None, 'URL to download the package' ),
        ( 'unpack-dir', None, 'Where to unpack the source' ),
        ( 'source-dir', None, 'Where the package will be after unpacking'),
        ( 'post-fetch', None, 'Callable to run after unpacking' ),
        ( 'member-filter', None, 'Function to filter tarball members' )
        ]
    def initialize_options(self):
        #
        # attributes fetched from build command
        #
        self.build_lib = None
        #
        # command attributes
        #
        self.package_name = None
        self.github_owner = None
        self.full_name = None
        self.version = None
        self.url = None
        self.unpack_dir = None
        self.source_dir = None
        self.post_fetch = None
        self.member_filter = None
        
    def finalize_options(self):
        self.set_undefined_options(
            'build', ('build_lib', 'build_lib'))
        if self.package_name is None:
            # "fetch_foo" has a default package name of "foo"
            for key, value in self.distribution.command_obj.iteritems():
                if value == self:
                    self.package_name = key.rpartition("_")[-1]
                    break
            else:
                raise DistutilsSetupError(
                    "package-name must be defined")
        if self.github_owner is None:
            self.github_owner = self.package_name
        if self.version is None and self.full_name is None:
            raise DistutilsSetupError(
                "Either one of or both the version and full_name must be defined")
        elif self.full_name is None:
            self.full_name = "{package_name}-{version}".format(**self.__dict__)
        else:
            self.full_name = self.full_name.format(**self.__dict__)
        if self.url is None and self.version is None:
            raise DistutilsSetupError(
                "Setup script must define this command's url")
        elif self.url is None:
            self.url = "https://github.com/{github_owner}/{package_name}/archive/{version}.tar.gz"
        self.url = self.url.format(**self.__dict__)
        if self.unpack_dir is None:
            self.unpack_dir = os.path.join(
                self.build_lib, self.package_name)
        else:
            self.unpack_dir = self.unpack_dir.format(**self.__dict__)
        if self.source_dir is None:
            self.source_dir = os.path.join(
                self.unpack_dir, self.full_name)
        else:
            self.source_dir = self.source_dir.format(**self.__dict__)
        
    def run(self):
        self.announce("Fetching " + self.url)
        up = urlparse.urlparse(self.url)
        target = os.path.join(os.path.dirname(self.source_dir),
                              up.path.rpartition('/')[-1])
        if not os.path.exists(self.source_dir):
            os.makedirs(self.source_dir)
        if up.scheme == 'ftp':
            fdsrc = urllib2.urlopen(self.url)
            with open(target, "wb") as fd:
                while True:
                    data = fdsrc.read()
                    if len(data) == 0:
                        break
                    fd.write(data)
        else:
            request = requests.get(self.url, stream=True)
            with open(target, "wb") as fd:
                for chunk in request.iter_content(chunk_size = 65536):
                    fd.write(chunk)
        members = None
        if target.lower().endswith(".zip"):
            tarball = zipfile.ZipFile(target)
            if self.member_filter is not None:
                members = filter(self.member_filter, tarball.namelist)
        else:
            tarball = tarfile.open(target)
            if self.member_filter is not None:
                def filter_fn(member, name_filter = self.member_filter):
                    return name_filter(member.name)
                members = filter(filter_fn, tarball.getmembers())
        tarball.extractall(self.unpack_dir, members = members)
        if self.post_fetch is not None:
            self.post_fetch(self)
        
class FetchFFTWWindowsBinaries(setuptools.Command):
    user_options = [ ("url=", None, "URL of FFTW Windows binaries"),
                     ("fftw-version", None, "Version of FFTW to use"),
                     ("install-dir=", None, "Install directory for binaries")]
    command_name = "fetch_fftw_binaries"
    
    def initialize_options(self):
        self.url = None
        self.build_lib = None
        self.install_dir = None
        self.fftw_version = None
        
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))
        if self.fftw_version is None:
            self.fftw_version = "3.3.2"
        if self.url is None:
            self.url = "ftp://ftp.fftw.org/pub/fftw/fftw-%s-dll64.zip" % \
                self.fftw_version
        if self.install_dir is None:
            self.install_dir = os.path.join(
                self.build_lib, "fftw", "install", 
                "fftw-%s" % self.fftw_version)
    def run(self):
        self.announce("Fetching FFTW binaries")
        request = urllib2.urlopen(self.url)
        fd = StringIO.StringIO()
        while True:
            b = request.read()
            if b is None or len(b) == 0:
                break
            fd.write(b)
        fd.seek(0)
        fftw_zip = zipfile.ZipFile(fd)
        fftw_zip.extractall(self.install_dir)
        #
        # Have to build libfftw-3.3.lib and others
        #
        for libname in ("libfftw3-3", "libfftw3f-3", "libfftw3l-3"):
            args = ["lib", "/machine:x64", 
                    "/def:%s.def" % os.path.join(self.install_dir, libname),
                    "/out:%s.lib" % os.path.join(self.install_dir, libname)]
            self.spawn(args)
        
class BuildLibhdf5(BuildWithCMake):
    def initialize_options(self):
        BuildWithCMake.initialize_options(self)
        self.zlib_install_dir = None
        self.szip_install_dir = None
        self.zlib_source_dir = None
        self.szip_source_dir = None
        self.zlib_make_dir = None
        self.szip_make_dir = None
        
    def finalize_options(self):
        BuildWithCMake.finalize_options(self)
        self.set_undefined_options(
            'build_zlib', 
            ('install_dir', 'zlib_install_dir'),
            ('target_dir', 'zlib_make_dir'),
            ('source_dir', 'zlib_source_dir'))
        self.set_undefined_options(
            'build_szip', 
            ('install_dir', 'szip_install_dir'),
            ('target_dir', 'szip_make_dir'),
            ('source_dir','szip_source_dir'))
        if is_win:
            szip_lib = 'szip.' + lib_ext
            zlib_lib = 'zlib.' + lib_ext
        else:
            szip_lib = 'libszip.' + lib_ext
            zlib_lib = 'libz.' + lib_ext
        for varname, cmake_type, install_dir, folder in (
            ("SZIP_LIBRARY_RELEASE", "FILEPATH", 
             self.szip_install_dir, os.path.join("lib", szip_lib)),
            ("SZIP_DIR", "PATH", self.szip_make_dir, None),
            ("SZIP_INCLUDE_DIR", "PATH", self.szip_install_dir, "include"),
            ("ZLIB_DIR", "PATH", self.zlib_make_dir, None),
            ("ZLIB_INCLUDE_DIR", "PATH", self.zlib_install_dir, "include"),
            ("ZLIB_LIBRARY_RELEASE", "FILEPATH", 
             self.zlib_install_dir, os.path.join("lib", zlib_lib))):
            if folder is not None:
                path = os.path.abspath(os.path.join(install_dir, folder))
            else:
                path = os.path.abspath(install_dir)
            self.extra_cmake_options.append(
                "\"-D{varname}:{cmake_type}={path}\"".format(**locals()))
            
class BuildH5Py(setuptools.Command):
    user_options = [("hdf5", None, "Location of libhdf5 install")]
    command_name = "build_h5py"
    
    def initialize_options(self):
        self.hdf5 = None
        self.source_dir = None
        self.temp_dir = None
        self.szip_install_dir = None
        self.zlib_install_dir = None
        
    def finalize_options(self):
        if self.hdf5 is None:
            self.set_undefined_options(
                'build_libhdf5', ('install_dir', 'hdf5'))
        if self.szip_install_dir is None:
            self.set_undefined_options(
                'build_szip', ('install_dir', 'szip_install_dir'))
        if self.zlib_install_dir is None:
            self.set_undefined_options(
                'build_zlib', ('install_dir', 'zlib_install_dir'))
        if self.source_dir is None:
            self.set_undefined_options(
                'fetch_h5py', ('source_dir', 'source_dir'))
        if self.temp_dir is None:
            self.temp_dir = os.path.join(os.path.dirname(self.source_dir), "tmp")
        
    def run(self):
        hdf5 = os.path.abspath(self.hdf5)
        for directory, ext in (('bin', 'dll'), ('lib', 'lib')):
            hdf5_dll = os.path.join(self.hdf5, directory, "hdf5."+ext)
            hdf5_hl_dll = os.path.join(self.hdf5, directory, "hdf5_hl."+ext)
            szip_dll = os.path.join(
                self.szip_install_dir, directory, "szip."+ext)
            zlib_dll = os.path.join(
                self.zlib_install_dir, directory, "zlib."+ext)
            for src, destfile in ((hdf5_dll, "h5py_hdf5."+ext),
                                  (hdf5_hl_dll, "h5py_hdf5_hl."+ext),
                                  (szip_dll, "szip.dll"+ext),
                                  (zlib_dll, "zlib.dll"+ext)):
                dest = os.path.join(self.hdf5, directory, destfile)
                self.copy_file(src, dest)
        
        old_curdir = os.path.abspath(os.curdir)
        os.chdir(os.path.abspath(self.source_dir))
        try:
            self.spawn([
                "python", "setup.py", "build", '"--hdf5=%s"' % hdf5])
            self.spawn(["python", "setup.py", "install"])
        finally:
            os.chdir(old_curdir)

class BuildBoost(setuptools.Command):
    command_name = "build_boost"
    user_options = [('install-dir', None, "Boost install directory")]
    
    def initialize_options(self):
        self.boost_src = None
        self.build_lib = None
        self.install_dir = None
        self.temp_dir = None
        
    def finalize_options(self):
        self.set_undefined_options(
            'build', ('build_lib', 'build_lib'))
        self.set_undefined_options(
            'fetch_boost', 
            ('source_dir', 'boost_src'))
        if self.install_dir is None:
            root, leaf = os.path.split(self.boost_src)
            self.install_dir = os.path.join(root, "install", leaf)
        if self.temp_dir is None:
            root, leaf = os.path.split(self.boost_src)
            self.temp_dir = os.path.join(root, "tmp", leaf)
    
    def run(self):
        self.bootstrap()
        self.build()
        
    def build(self):
        args = ["b2", '"--stagedir=%s"' % os.path.abspath(self.install_dir),
                '"--build-dir=%s"' % os.path.abspath(self.temp_dir),
                "--with-python", 
                "link=shared", "variant=release", "threading=multi",
                "address-model=64",
                "runtime-link=shared"]
        args.append("stage")
        self.spawn(args)
        
    def bootstrap(self):
        #
        # Boost has a bootstrapping script that builds bjam / b2
        # The single parameter to the script is the toolchain to use
        #
        if is_win:
            bootstrap_script = "bootstrap.bat"
        else:
            bootstrap_script = "boostrap.sh"
        args = [bootstrap_script]
        if is_win:
	    self.toolset = toolset
            args.append(self.toolset)
        else:
            self.toolset = None
        self.spawn(args)
        if is_win:
            #
            # Build the project configuration file to use the version of
            # MSVC used to compile Python (we may want to change this
            # to detect the SDK)
            #
            from distutils.sysconfig import get_config_var, get_python_inc
            def fixpath(path):
                path = path.replace("\\", "/")
                return path
            libs_path = fixpath(get_config_var('LIBDIR'))
            python_path = fixpath(sys.executable)
            include_path = fixpath(get_python_inc())
            project_config_path = os.path.join(
                self.boost_src, "project-config.jam")
            with open(project_config_path, "w") as fd:
                fd.write("using msvc : %s ;\n" % build_version)
                fd.write('using python : %d.%d : "%s" : "%s" : "%s" ;' % 
                         (sys.version_info.major, sys.version_info.minor,
                          python_path, include_path, libs_path))
    
    def spawn(self, args):
        #
        # Must... change... directory...
        #
        old_cwd = os.path.abspath(os.curdir)
        os.chdir(os.path.abspath(self.boost_src))
        try:
            distutils.spawn.spawn(
                args, verbose = self.verbose, dry_run=self.dry_run)
        finally:
            os.chdir(old_cwd)
            
class BuildVigra(BuildWithCMake):
    command_name = 'build_vigra'
    
    def initialize_options(self):
        BuildWithCMake.initialize_options(self)
        self.source_dir = None
        self.install_dir = None
        self.zlib_install_dir = None
        self.zlib_library = None
        self.zlib_include_dir = None
        self.libhdf5_install_dir = None
        self.hdf5_core_library = None
        self.hdf5_hl_library = None
        self.szip_install_dir = None
        self.szip_library = None
        self.hdf5_include_dir = None
        self.fftw_install_dir = None
        self.fftw_include_dir = None
        self.fftw_library = None
        self.boost_install_dir = None
        self.boost_python_library = None
        self.boost_src = None
        self.boost_include_dir = None
        self.boost_library_dir = None
        
    def finalize_options(self):
        BuildWithCMake.finalize_options(self)
        self.set_undefined_options(
            'build_szip', ('install_dir', 'szip_install_dir'))
        if self.szip_library is None:
            self.szip_library = os.path.join(
                self.szip_install_dir, 'lib', 'szip.%s' % lib_ext)
        self.extra_cmake_options.append(
        '"-DHDF5_SZ_LIBRARY:FILEPATH=%s"' % self.szip_library)
        
        if is_win:
            self.set_undefined_options(
                'build_zlib', ('install_dir', 'zlib_install_dir'))
            self.set_undefined_options(
                'build_libhdf5', ('install_dir', 'libhdf5_install_dir'))
            self.set_undefined_options(
                'build_boost', 
                ('install_dir', 'boost_install_dir'),
                ('boost_src', 'boost_src'))
            self.set_undefined_options(
                'fetch_fftw_binaries', ('source_dir', 'fftw_install_dir'))
            
            if self.zlib_library is None:
                zlib = 'zlib.' + lib_ext
            self.zlib_library = os.path.join(
                self.zlib_install_dir, 'lib', zlib)
            self.extra_cmake_options.append(
                '"-DZLIB_LIBRARY:FILEPATH=%s"' % self.zlib_library)
            self.extra_cmake_options.append(
                '"-DHDF5_Z_LIBRARY:FILEPATH=%s"' % self.zlib_library)
        
            if self.zlib_include_dir is None:
                self.zlib_include_dir = os.path.join(
                    self.zlib_install_dir, "include")
            self.extra_cmake_options.append(
                '"-DZLIB_INCLUDE_DIR:PATH=%s"' % self.zlib_include_dir)
        
            if self.hdf5_core_library is None:
                self.hdf5_core_library = os.path.join(
                    self.libhdf5_install_dir, 'lib', "hdf5.%s" % lib_ext)
            self.extra_cmake_options.append(
                '"-DHDF5_CORE_LIBRARY:FILEPATH=%s"' % self.hdf5_core_library)
            if self.hdf5_hl_library is None:
                self.hdf5_hl_library = os.path.join(
                    self.libhdf5_install_dir, "lib", "hdf5_hl.%s" % lib_ext)
            self.extra_cmake_options.append(
                '"-DHDF5_HL_LIBRARY:FILEPATH=%s"' % self.hdf5_hl_library)
            if self.hdf5_include_dir is None:
                self.hdf5_include_dir = os.path.join(
                    self.libhdf5_install_dir, "include")
            self.extra_cmake_options.append(
                '"-DHDF5_INCLUDE_DIR:PATH=%s"' % self.hdf5_include_dir)
            
            if self.fftw_include_dir is None:
                self.fftw_include_dir = os.path.join(
                    self.fftw_install_dir)
            self.extra_cmake_options.append(
                '"-DFFTW3_INCLUDE_DIR:PATH=%s"' % 
                os.path.abspath(self.fftw_install_dir))
            
            if self.fftw_library is None:
                self.fftw_library = os.path.join(
                    self.fftw_install_dir, "libfftw3-3.%s" % lib_ext)
            self.extra_cmake_options.append(
                '"-DFFTW3_LIBRARY:FILEPATH=%s"' % 
                os.path.abspath(self.fftw_library))
    
            #
            # BOOST configuration
            #
            self.extra_cmake_options.append(
                '"-DBOOST_ROOT:PATH=%s"' % os.path.abspath(self.boost_src))
            boost_libname = "boost_python-%s-mt-1_53.lib" % toolset
            if self.boost_library_dir is None:
                self.boost_library_dir = os.path.abspath(os.path.join(
                    self.boost_install_dir, "lib"))
            if self.boost_python_library is None:
                self.boost_python_library = os.path.join(
                    self.boost_library_dir, boost_libname)
            self.extra_cmake_options.append(
                '"-DBoost_PYTHON_LIBRARY_RELEASE:FILEPATH=%s"' % 
                self.boost_python_library)
            self.extra_cmake_options.append(
                '"-DBoost_LIBRARY_DIR:PATH=%s"' % self.boost_library_dir)
            
            if self.boost_include_dir is None:
                self.boost_include_dir = os.path.abspath(self.boost_src)
            self.extra_cmake_options.append(
                '"-DBoost_INCLUDE_DIR:PATH=%s"' % self.boost_include_dir)
	    self.extra_cmake_options.append(
	        r'"-DCMAKE_CXX_FLAGS:STRING=/EHsc"')
        
    def rewrite_windows_setup(self, setup_directory):
        #
        # Vigra's CMake looks like it confuses .lib and .dll files
        # Or maybe I am setting things up wrong
        # Rewrite setup.py
        #
        setup_path = os.path.join(setup_directory, "setup.py")
        with open(setup_path, "r") as fd:
            lines = fd.readlines()
        with open(setup_path, "w") as fd:
            state = 'before'
            for line in lines:
                if state == 'before' and line.startswith('dlls = ['):
                    state = 'during'
                if state == 'during':
                    line = line\
                        .replace('/lib/', '/bin/')\
                        .replace('.lib', '.dll')\
                        .replace('/build/bin/', '/build/lib/')\
                        .replace('/bin/boost_python', '/lib/boost_python')
                    if line.strip().endswith("]"):
                        state = 'whereisctypes'
                if state == 'whereisctypes':
                    # Get rid of the search using ctypes
                    index = line.find('ctypes.util.find')
                    if index >= 0:
                        line = line[:index] + "d\n"
                        state = 'no-pyqt'
                if state == 'no-pyqt':
                    index = line.find(", 'vigra.pyqt'")
                    if index > 0:
                        line = line[:index] + line.strip()[-2:] + "\n"
                fd.write(line)
        
    def run(self):
        BuildWithCMake.run(self)
        setup_directory = os.path.abspath(os.path.join(self.target_dir, "vigranumpy"))
        #if is_win:
        #    self.rewrite_windows_setup(setup_directory)
        old_cwd = os.path.abspath(os.curdir)
        os.chdir(setup_directory)
        try:
            self.spawn(["nmake", "install"])
        finally:
            os.chdir(old_cwd)
	#
	# This is a non-standard way of putting the DLLs into the
	# vigra package, but the whole install process is very non-standard
	#
	site_packages = distutils.sysconfig.get_python_lib()
	vigra_target = os.path.join(site_packages, "vigra")
	boost_python_dll = os.path.splitext(self.boost_python_library)[0]+".dll"
	fftw_dll = os.path.splitext(self.fftw_library)[0] + ".dll"
	impex_dll = os.path.join(
	    self.target_dir, "src", "impex", "vigraimpex.dll")
	szip_dll = os.path.join(self.szip_install_dir, "bin", "szip.dll")
	hdf_dlls = [
	    os.path.join(self.libhdf5_install_dir, "bin", libname+".dll")
	    for libname in ("hdf5", "hdf5_hl")]
	zlib_dll = os.path.join(self.zlib_install_dir, "bin", "zlib.dll")
	all_dlls = [boost_python_dll, fftw_dll, impex_dll, szip_dll]
	all_dlls += hdf_dlls
	all_dlls.append(zlib_dll)
	for dll_path in all_dlls:
	    filename = os.path.split(dll_path)[1]
	    self.copy_file(dll_path, os.path.join(vigra_target, filename))
            
class InstallIlastik(setuptools.Command):
    command_name = 'install_ilastik'
    user_options = []
    
    def initialize_options(self):
        self.ilastik_src = None
        
    def finalize_options(self):
        if self.ilastik_src is None:
            self.set_undefined_options(
                'fetch_ilastik', ('source_dir', 'ilastik_src'))
    
    def run(self):
        old_curdir = os.path.abspath('.')
        os.chdir(os.path.abspath(self.ilastik_src))
        try:
            self.spawn(['python', 'setup.py', 'build', 'install'])
        finally:
            os.chdir(old_curdir)
        
class BuildIlastik(distutils.command.build.build):
    command_name = 'build'
    user_options = list(distutils.command.build.build.user_options)
    user_options.append(("cmake=", None, "Location of the CMake executable"))
    
    def initialize_options(self):
        distutils.command.build.build.initialize_options(self)
        self.cmake = None
    
    def needs_h5py(self):
        try:
            import h5py
            return False
        except ImportError:
            return True
        
    sub_commands = distutils.command.build.build.sub_commands + \
        [('fetch_szip', None),
         ('build_szip', None)]
    
    if is_win:
        sub_commands += [
            ('fetch_zlib', None),
            ('build_zlib', None),
            ('fetch_libhdf5', None),
            ('build_libhdf5', None),
            ('fetch_h5py', needs_h5py),
            ('build_h5py', needs_h5py),
            ('fetch_boost', None),
            ('build_boost', None),
            ('fetch_fftw_binaries', None)]
    sub_commands += [
        ('fetch_vigra', None),
        ('build_vigra', None),
        ('fetch_ilastik', None),
        ('install_ilastik', None)]
    
def patch_szip(cmd):
    '''Patch the CMakeLists file to include ricehdf.h'''
    expected_hash = 'fb8f11ef336e8d0a4d306aa479907979'
    path = os.path.join(cmd.source_dir, "src", "CMakeLists.txt")
    h = hashlib.md5(open(path, "rb").read())
    if h.hexdigest() == expected_hash:
        #
        # SZip CMake needs patching. It excludes ricehdf.h
        #
        handle, filename = tempfile.mkstemp(suffix=".h")
        fd = os.fdopen(handle, "w")
        with open(path) as fdsrc:
            for i, line in enumerate(fdsrc):
                line_number = i+1
                if line_number >= 19 and line_number < 22:
                    # These are private header files and ricehdf.h
                    # is the only one. So we delete the section.
                    if line_number == 20:
                        # This is the line that includes ricehdf.h
                        saved = line
                    continue
                elif line_number == 24:
                    # put the line in the public headers
                    fd.write(saved)
                elif line_number == 28:
                    # remove the private headers from the library def
                    line = line.replace("${SZIP_HDRS} ", "")
                fd.write(line)
        fd.close()
        shutil.copyfile(filename, path)

def filter_boost(name):
    '''Filter out the image files in order to reduce the tarball size
    
    tarfile chokes, running on Windows, while unpacking random image files
    '''
    return not any([name.lower().endswith(ext) 
                   for ext in (".png", ".html")])
        
def build_fftw_libs(cmd):
    '''Build the Windows .lib files for the FFTW dlls'''
    for libname in ("libfftw3-3", "libfftw3f-3", "libfftw3l-3"):
        args = ["lib", "/machine:x64", 
                "/def:%s.def" % os.path.join(cmd.source_dir, libname),
                "/out:%s.lib" % os.path.join(cmd.source_dir, libname)]
        cmd.spawn(args)
        
def patch_vigra(cmd):
    '''Patch Vigra to deal with future issues
    
    missing ptrdiff_t
    https://gcc.gnu.org/gcc-4.6/porting_to.html
    '''
    config_hxx_path = os.path.join(
        cmd.source_dir, "include", "vigra", "config.hxx")
    pattern = r"\s*#include\s+<cstddef>"
    with open(config_hxx_path, "r") as fdsrc:
        lines = fdsrc.readlines()
    if any([re.search(pattern, line) for line in lines]):
        return
    
    lines.insert(len(lines) - 2, "#include <cstddef>\n")
    with open(config_hxx_path, "w") as fd:
        fd.write("".join(lines))
    
    #
    # Put the BOOST toolset def in
    #
    cmakelists_path = os.path.join(cmd.source_dir, "CMakeLists.txt")
    if is_win:
	with open(cmakelists_path, "r") as fd:
	    lines = fd.readlines()
        with open(cmakelists_path, "w") as fd:
	    first = True
	    for line in lines:
		fd.write(line)
		if re.search(r"IF\s\(MSVC\)", line) and first:
		    fd.write('ADD_DEFINITIONS(-DBOOST_LIB_TOOLSET=\\"%s\\")\n' %
		             toolset)
		    first = False
    
try:
    import h5py
    libhdf5_version = h5py.version.hdf5_version
except:
    libhdf5_version = "1.8.11"

try:
    command_classes = dict([(cls.command_name, cls) for cls in (
            BuildIlastik, BuildH5Py)])
    for build_class in ('build_zlib', 'build_szip'):
        command_classes[build_class] = BuildWithCMake
    for fetch_command in ('fetch_libhdf5', 'fetch_szip', 'fetch_zlib',
                          'fetch_boost', 'fetch_ilastik', 'fetch_fftw',
                          'fetch_fftw_binaries', 'fetch_vigra', 'fetch_h5py'):
        command_classes[fetch_command] = FetchSource
    command_classes['build_boost'] = BuildBoost
    command_classes['build_libhdf5'] = BuildLibhdf5
    command_classes['build_vigra'] = BuildVigra
    command_classes['install_ilastik'] = InstallIlastik
    result = setuptools.setup(
        cmdclass=command_classes,
        options = {
            'build_zlib': dict(
                src_command='fetch_zlib',
                extra_cmake_options = ["-DBUILD_SHARED_LIBS:BOOL=\"1\""]),
            'build_szip': dict(
                src_command='fetch_szip',
                extra_cmake_options = ["-DBUILD_SHARED_LIBS:BOOL=\"1\""]),
            'build_libhdf5': dict(
                src_command='fetch_libhdf5',
                extra_cmake_options = [
                    '-DHDF5_ENABLE_SZIP_ENCODING:BOOL="1"',
                    '-DBUILD_SHARED_LIBS:BOOL="1"',
                    '-DHDF5_ENABLE_Z_LIB_SUPPORT:BOOL="1"',
                    '-DHDF5_ENABLE_SZIP_SUPPORT:BOOL="1"',
                    '-DHDF5_BUILD_HL_LIB:BOOL="1"',
                    '-DSZIP_USE_EXTERNAL:BOOL="0"',
                    '-DHDF5_ALLOW_EXTERNAL_SUPPORT:BOOL="0"',
                    '-DHDF5_BUILD_CPP_LIB:BOOL="1"',
                    '-DZLIB_USE_EXTERNAL:BOOL="0"',
                    '-DCPACK_SOURCE_ZIP:BOOL="0"',
                    "-DBUILD_SHARED_LIBS:BOOL=\"1\""]),
            'build_vigra': dict(
                src_command='fetch_vigra',
                extra_cmake_options = [
                    '-DCPACK_SOURCE_ZIP:BOOL="0"',
                    '-DCPACK_SOURCE_7Z:BOOL="0"'],
                do_install = False
            ),
            'fetch_szip': {
                'version': '2.1',
                'url': "https://www.hdfgroup.org/ftp/lib-external/{package_name}/{version}/src/{package_name}-{version}.tar.gz",
                'post_fetch': patch_szip
            }, 
            'fetch_zlib': {
                'version': '1.2.5',
                'url': "https://www.hdfgroup.org/ftp/lib-external/{package_name}/{package_name}-{version}.tar.gz"
            },
            'fetch_libhdf5': {
                'package_name': 'hdf5',
                'version': libhdf5_version,
                'url': "https://www.hdfgroup.org/ftp/HDF5/releases/{package_name}-{version}/src/{package_name}-{version}.zip"
                },
            'fetch_boost': {
                'version': '1.53.0',
                'full_name': '{package_name}_1_53_0',
                'url': "http://cellprofiler.org/linux/SOURCES/{full_name}.tar.bz2",
                'member_filter': filter_boost
                },
            'fetch_h5py': {
                'version': '2.3.1'
                },
            'fetch_fftw': {
                'version': '3.2.2',
                'url': "http://cellprofiler.org/linux/SOURCES/{package_name}-{version}.tar.gz"
                },
            'fetch_fftw_binaries': {
                'package_name': 'fftw',
                'version': "3.3.2",
                'url': "ftp://ftp.fftw.org/pub/{package_name}/{package_name}-{version}-dll64.zip",
                'unpack_dir': '{build_lib}/{package_name}/{full_name}',
                'source_dir': '{unpack_dir}',
                'post_fetch': build_fftw_libs
                },
            'fetch_vigra': {
                'version': '1.7.1',
                'url': "http://cellprofiler.org/linux/SOURCES/{package_name}-{version}-src.tar.gz",
                'post_fetch': patch_vigra
                },
            'fetch_ilastik': {
                'version': 'v0.5.05',
                'url':"http://cellprofiler.org/linux/SOURCES/{full_name}.tar.gz"
                }
        }
    )
except:
    import traceback
    traceback.print_exc()
    sys.exit(1)
sys.exit(0)
