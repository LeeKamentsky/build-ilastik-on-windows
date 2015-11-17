import setuptools
import distutils.command.build
import distutils.spawn
import hashlib
import os
import requests
import shutil
import StringIO
import sys
import tarfile
import tempfile
import urllib2
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
        
    def finalize_options(self):
        self.set_undefined_options(
            'build', ('build_lib', 'build_lib'))
        self.set_undefined_options('build', ('cmake', 'cmake'))
        if self.cmake is None and is_win:
            path = r"C:\Program Files (x86)\CMake\bin"
            if os.path.exists(path):
                self.cmake = os.path.join(path, "cmake")
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
    
    def use_custom_install_dir(self):
        '''Should we honor the install directory or use the default?

        Override this and return False if CMake should install to the
        package's default location. Return True to install to a location
        in the build tree.
        '''
        return True
    
    def run(self):
        cmake_args = [self.cmake]
        cmake_args += ["-G", self.get_cmake_generator()]
        if self.use_custom_install_dir() and not is_win:
            cmake_args.append(
                '"-DCMAKE_INSTALL_PREFIX:PATH=%s"' % 
                os.path.abspath(self.install_dir))
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
            self.spawn(cmake_args)
            os.chdir(target_dir)
            self.spawn([self.get_make_program()])
            if (not self.use_custom_install_dir()) or is_win:
                self.spawn([self.get_make_program(), "install"])
            else:
                self.spawn([self.get_make_program(),
                            "DESTDIR=%s" % os.path.abspath(self.install_root),
                            "install"])
        finally:
            os.chdir(old_dir)
        
class FetchSZipSource(setuptools.Command):
    command_name = "fetch_szip"
    user_options = []
    
    def initialize_options(self):
        self.build_lib = None
    
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))
        self.szip_path = os.path.join(self.build_lib, "szip")
        self.url = \
            "https://www.hdfgroup.org/ftp/lib-external/szip/2.1/src/szip-2.1.tar.gz"
        self.source_dir = os.path.join(self.szip_path, "szip-2.1")
    
    def run(self):
        self.announce("Fetching SZIP source")
        request = requests.get(self.url, stream=False)
        szip_tar = tarfile.open("szip-2.1.tar.gz",
                                fileobj = StringIO.StringIO(request.content))
        szip_tar.extractall(self.szip_path)
        expected_hash = 'fb8f11ef336e8d0a4d306aa479907979'
        path = os.path.join(self.source_dir, "src", "CMakeLists.txt")
        h = hashlib.md5(open(path, "rb").read())
        if h.hexdigest() == expected_hash:
            self.patch_cmakelists(path)
        
    def patch_cmakelists(self, path):
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
        
class FetchZlibSource(setuptools.Command):
    command_name = "fetch_zlib"
    user_options = []
    
    def initialize_options(self):
        self.build_lib = None
    
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))
        self.zlib_path = os.path.join(self.build_lib, "zlib")
        self.url = \
            "https://www.hdfgroup.org/ftp/lib-external/zlib/zlib-1.2.5.tar.gz"
        self.source_dir = os.path.join(self.zlib_path, "zlib-1.2.5")
    
    def run(self):
        self.announce("Fetching ZLib source")
        request = requests.get(self.url, stream=False)
        zlib_tar = tarfile.open("zlib-1.2.5.tar.gz",
                                fileobj = StringIO.StringIO(request.content))
        zlib_tar.extractall(self.zlib_path)
        
class FetchLibHDF5Source(setuptools.Command):
    user_options = [ ("version=", "v", "version to fetch"),
                     ("url=", None, "URL of HDF5 source")]
    command_name = "fetch_libhdf5"
    try:
        import h5py
        default_version = h5py.version.hdf5_version
    except:
        default_version = "1.8.11"
    @staticmethod
    def make_url(version):
        return (
            "https://www.hdfgroup.org/ftp/HDF5/releases/"
            "hdf5-{version}/src/hdf5-{version}.zip").format(
                version=version)

    def initialize_options(self):
        self.version = self.default_version
        self.url = self.make_url(self.default_version)
        self.build_lib = None
        
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))

        if self.version != self.default_version and \
           self.url == self.make_url(self.default_version):
            self.url = self.make_url(self.version)
        self.announce("Using URL=%s" % self.url, level=2)
        self.hdf5lib_dest = os.path.join(self.build_lib, "libhdf5")
        self.source_dir = os.path.join(self.hdf5lib_dest,
                                       "hdf5-%s" % self.version)
        self.announce("Extracting to %s" % self.hdf5lib_dest)
        
    def run(self):
        self.announce("Fetching libhdf5 source")
        request = requests.get(self.url, stream=False)
        hdf5_zip = zipfile.ZipFile(StringIO.StringIO(request.content))
        hdf5_zip.extractall(self.hdf5lib_dest)
        
class FetchBoostSource(setuptools.Command):
    user_options = [ ("url=", None, "URL of Boost source")]
    command_name = "fetch_boost"

    def initialize_options(self):
        self.url = "http://cellprofiler.org/linux/SOURCES/boost_1_53_0.tar.bz2"
        self.build_lib = None
        self.source_dir = None
        self.boost_dest = None
        
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))

        self.announce("Using URL=%s" % self.url, level=2)
        self.boost_dest = os.path.join(self.build_lib, "boost")
        self.source_dir = os.path.join(self.boost_dest, "boost_1_53_0")
        
    def run(self):
        self.announce("Fetching Boost source")
        request = requests.get(self.url, stream=False)
        boost_tar = tarfile.open("boost_1_53_0.tar.bz2",
                                 fileobj = StringIO.StringIO(request.content))
        def my_filter(x):
            if x.name.partition("/")[2].startswith("doc"):
                return False
            if x.name.find("doc/html") >= 0:
                return False
            return True
        members = filter(my_filter,boost_tar.getmembers())
        
        boost_tar.extractall(self.boost_dest, members)
        
class FetchH5PySource(setuptools.Command):
    user_options = [ ("url=", None, "URL of H5Py source"),
                     ("version=", None, "H5py version, e.g. 2.3.1")]
    command_name = "fetch_h5py"

    def initialize_options(self):
        self.url = None
        self.build_lib = None
        self.source_dir = None
        self.boost_dest = None
        
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))
        if self.version is None:
            self.version = "2.3.1"
        if self.url is None:
            self.url = \
                "https://github.com/h5py/h5py/archive/%s.tar.gz" % self.version

        self.announce("Using URL=%s" % self.url, level=2)
        self.h5py_dest = os.path.join(self.build_lib, "h5py")
        self.source_dir = os.path.join(self.h5py_dest, 
                                       "h5py-%s" % self.version)
        
    def run(self):
        self.announce("Fetching H5Py source")
        request = requests.get(self.url, stream=False)
        h5py_tar = tarfile.open("%s.tar.gz" % self.version,
                                 fileobj = StringIO.StringIO(request.content))
        h5py_tar.extractall(self.h5py_dest)
        
class FetchFFTWSource(setuptools.Command):
    user_options = [ ("url=", None, "URL of FFTW source")]
    command_name = "fetch_fftw"

    def initialize_options(self):
        self.url = "http://cellprofiler.org/linux/SOURCES/fftw-3.2.2.tar.gz"
        self.build_lib = None
        self.source_dir = None
        self.fftw_dest = None
        
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))

        self.announce("Using URL=%s" % self.url, level=2)
        self.fftw_dest = os.path.join(self.build_lib, "fftw")
        self.source_dir = os.path.join(self.fftw_dest, "fftw-3.2.2")
        
    def run(self):
        self.announce("Fetching FFTW source")
        request = requests.get(self.url, stream=False)
        fftw_tar = tarfile.open(
            "fftw-3.2.2.tar.gz",
            fileobj =StringIO.StringIO(request.content))
        fftw_tar.extractall(self.fftw_dest)
        
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
        
class FetchVigraSource(setuptools.Command):
    user_options = [ ("url=", None, "URL of Vigra source")]
    command_name = "fetch_vigra"

    def initialize_options(self):
        self.url = "http://cellprofiler.org/linux/SOURCES/vigra-1.7.1-src.tar.gz"
        self.build_lib = None
        self.source_dir = None
        self.vigra_dest = None
        
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))

        self.announce("Using URL=%s" % self.url, level=2)
        self.vigra_dest = os.path.join(self.build_lib, "vigra")
        self.source_dir = os.path.join(self.vigra_dest, "vigra-1.7.1")
        
    def run(self):
        self.announce("Fetching Vigra source")
        request = requests.get(self.url, stream=False)
        vigra_tar = tarfile.open(
            "vigra-1.7.1.tar.gz",
            fileobj =StringIO.StringIO(request.content))
        vigra_tar.extractall(self.vigra_dest)
    
class FetchIlastikSource(setuptools.Command):
    user_options = [ ("url=", None, "URL of Ilastik source")]
    command_name = "fetch_ilastik"
    
    def initialize_options(self):
        self.url = \
            "http://cellprofiler.org/linux/SOURCES/ilastik-v0.5.05.tar.gz"
        self.build_lib = None
        self.source_dir = None
        self.ilastik_dest = None
        
    def finalize_options(self):
        self.set_undefined_options(
            "build", ('build_lib', 'build_lib'))

        self.announce("Using URL=%s" % self.url, level=2)
        self.ilastik_dest = os.path.join(self.build_lib, "ilastik")
        self.source_dir = os.path.join(self.ilastik_dest, "ilastik-v0.5.05")
        
    def run(self):
        self.announce("Fetching Ilastik source")
        request = requests.get(self.url, stream=False)
        ilastik_tar = tarfile.open(
            "ilastik-v0.5.05.tar.gz",
            fileobj =StringIO.StringIO(request.content))
        ilastik_tar.extractall(self.ilastik_dest)
        
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
        for varname, cmake_type, install_dir, folder in (
            ("SZIP_LIBRARY_RELEASE", "FILEPATH", 
             self.szip_install_dir, "lib/szip.lib"),
            ("SZIP_DIR", "PATH", self.szip_make_dir, None),
            ("SZIP_INCLUDE_DIR", "PATH", self.szip_install_dir, "include"),
            ("ZLIB_DIR", "PATH", self.zlib_make_dir, None),
            ("ZLIB_INCLUDE_DIR", "PATH", self.zlib_install_dir, "include"),
            ("ZLIB_LIBRARY_RELEASE", "FILEPATH", 
             self.zlib_install_dir, "lib/zlib.lib")):
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
        
    def finalize_options(self):
        if self.hdf5 is None:
            self.set_undefined_options(
                'build_libhdf5', ('install_dir', 'hdf5'))
        if self.source_dir is None:
            self.set_undefined_options(
                'fetch_h5py', ('source_dir', 'source_dir'))
        
    def run(self):
        hdf5 = os.path.abspath(self.hdf5)
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
            build_version = get_build_version()
            self.toolset = "vc%d" % (int(build_version))
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
            'build_zlib', ('install_dir', 'zlib_install_dir'))
        self.set_undefined_options(
            'build_libhdf5', ('install_dir', 'libhdf5_install_dir'))
        self.set_undefined_options(
            'build_szip', ('install_dir', 'szip_install_dir'))
        self.set_undefined_options(
            'build_boost', 
            ('install_dir', 'boost_install_dir'),
            ('boost_src', 'boost_src'))
        if is_win:
            self.set_undefined_options(
                'fetch_fftw_binaries', ('install_dir', 'fftw_install_dir'))
            
        else:
            self.set_undefined_options(
                'build_fftw', ('install_dir', 'fftw_install_dir'))
        
        if self.zlib_library is None:
            self.zlib_library = os.path.join(
                self.zlib_install_dir, 'lib', 'zlib.lib')
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
                self.libhdf5_install_dir, 'lib', "hdf5.lib")
        self.extra_cmake_options.append(
            '"-DHDF5_CORE_LIBRARY:FILEPATH=%s"' % self.hdf5_core_library)
        if self.hdf5_hl_library is None:
            self.hdf5_hl_library = os.path.join(
                self.libhdf5_install_dir, "lib", "hdf5_hl.lib")
        self.extra_cmake_options.append(
            '"-DHDF5_HL_LIBRARY:FILEPATH=%s"' % self.hdf5_hl_library)
        
        if self.szip_library is None:
            self.szip_library = os.path.join(
                self.szip_install_dir, 'lib', 'szip.lib')
        self.extra_cmake_options.append(
        '"-DHDF5_SZ_LIBRARY:FILEPATH=%s"' % self.szip_library)
        
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
                self.fftw_install_dir, "libfftw3-3.lib")
        self.extra_cmake_options.append(
            '"-DFFTW3_LIBRARY:FILEPATH=%s"' % 
            os.path.abspath(self.fftw_library))

        #
        # BOOST configuration
        #
        self.extra_cmake_options.append(
            '"-DBOOST_ROOT:PATH=%s"' % os.path.abspath(self.boost_src))
        if is_win:
            vcver = int(get_build_version()) * 10
            boost_libname = "boost_python-vc%d-mt-1_53.lib" % vcver
        else:
            raise NotImplementedError("Need to manufacture library name for other platforms")
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
    
    def use_custom_install_dir(self):
        '''Install Vigra to Python'''
        return False
        
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
            self.spawn(['python', 'setup.py'])
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
        [(FetchZlibSource.command_name, None),
         ('build_zlib', None),
         (FetchSZipSource.command_name, None),
         ('build_szip', None),
         (FetchLibHDF5Source.command_name, None),
         ('build_libhdf5', None),
         (FetchH5PySource.command_name, needs_h5py),
         (BuildH5Py.command_name, needs_h5py),
         ('fetch_boost', None),
         ('build_boost', None),
         ('fetch_vigra', None),
         ('build_vigra', None),
         ('fetch_ilastik', None),
         ('install_ilastik', None)]
    
    if is_win:
        sub_commands.append(('fetch_fftw_binaries', None))
    else:
        sub_commands += [('fetch_fftw', None), ('build_fftw', None)]
    sub_commands += [         
         ('fetch_vigra', None)]
    
            
        


try:
    command_classes = dict([(cls.command_name, cls) for cls in (
            BuildIlastik, FetchLibHDF5Source, FetchSZipSource,
            FetchZlibSource, FetchBoostSource, FetchIlastikSource,
            FetchFFTWSource, FetchFFTWWindowsBinaries, FetchVigraSource,
            FetchH5PySource, BuildH5Py)])
    for build_class in ('build_zlib', 'build_szip'):
        command_classes[build_class] = BuildWithCMake
    command_classes['build_libhdf5'] = BuildLibhdf5
    command_classes['build_boost'] = BuildBoost
    command_classes['build_vigra'] = BuildVigra
    result = setuptools.setup(
        cmdclass=command_classes,
        options = {
            'build_zlib': dict(
                src_command=FetchZlibSource.command_name,
                extra_cmake_options = ["-DBUILD_SHARED_LIBS:BOOL=\"1\""]),
            'build_szip': dict(
                src_command=FetchSZipSource.command_name,
                extra_cmake_options = ["-DBUILD_SHARED_LIBS:BOOL=\"1\""]),
            'build_libhdf5': dict(
                src_command=FetchLibHDF5Source.command_name,
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
                src_command=FetchVigraSource.command_name,
                extra_cmake_options = [
                    '-DCPACK_SOURCE_ZIP:BOOL="0"',
                    '-DCPACK_SOURCE_7Z:BOOL="0"']
            )
            }
    )
except:
    import traceback
    traceback.print_exc()
    sys.exit(1)
sys.exit(0)
