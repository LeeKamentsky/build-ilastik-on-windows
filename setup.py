import setuptools
import distutils.command.build
import distutils.spawn
import h5py
import os
import requests
import StringIO
import sys
import tarfile
import zipfile

is_win = sys.platform.startswith('win')

class BuildWithCMake(setuptools.Command):
    user_options = [ 
        ("cmake", None, "Location of CMake executables")
    ]
    
    def initialize_options(self):
        self.build_lib = None
        self.cmake = None
        self.source_dir = None
        self.target_dir = None
        self.src_command = None
        self.extra_cmake_options = []
        
    def finalize_options(self):
        self.set_undefined_options(
            'build', ('build_lib', 'build_lib'))
        if self.cmake is None:
            path = r"C:\Program Files (x86)\CMake\bin"
            if os.path.exists(path):
                self.cmake = path
            else:
                raise distutils.command.build.DistutilsOptionError(
                "CMake is not installed in the default location and --cmake not specified")
        if self.source_dir is None:
            self.set_undefined_options(
                self.src_command, ("source_dir", "source_dir"))
    
    def get_sub_commands(self):
        if os.path.exists(self.source_dir):
            return []
        return [self.src_command]
    
    def get_cmake_generator(self):
        if is_win:
            return "NMake Makefiles"
        else:
            return "Unix Makefules"
        
    def get_make_program(self):
        if is_win:
            return "nmake"
        return "make"
    
    def run(self):
        cmake_args = [
            os.path.join(self.cmake, "cmake")]
        cmake_args += ["-G", self.get_cmake_generator()]
        cmake_args += self.extra_cmake_options
        # I don't like changing directories. I can't see any way to make
        # cmake build its makefiles in another directory
        old_dir = os.path.abspath(os.curdir)
        os.chdir(self.source_dir)
        try:
            self.spawn(cmake_args)
            self.spawn([self.get_make_program()])
        finally:
            os.chdir(old_dir)
        
    def spawn(self, args):
        '''Spawn a process using the correct compile environment'''
        if sys.platform.startswith('win'):
            if not hasattr(self, 'vcvarsall'):
                from distutils.msvc9compiler \
                     import find_vcvarsall, get_build_version
                self.vcvarsall = find_vcvarsall(get_build_version())
            if self.vcvarsall is not None:
                args = [self.vcvarsall] + args
        return distutils.spawn.spawn(
            args, verbose = self.verbose, dry_run=self.dry_run)

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
    default_version = h5py.version.hdf5_version
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
        self.set_undefined_options(
            FetchSZipSource.command_name,
            ('zlib_path', 'zlib_path'))
        self.set_undefined_options(
            FetchZlibSource.command_name,
            ('szip_path', 'szip_path'))

        if self.version != self.default_version and \
           self.url == self.make_url(self.default_version):
            self.url = self.make_url(self.version)
        self.announce("Using URL=%s" % self.url, level=2)
        self.hdf5lib_dest = os.path.join(self.build_lib, "libhdf5")
        self.source_dir = os.path.join(self.hdf5lib_dest,
                                       "hdf5-%s" % self.version)
        self.announce("Extracting to %s" % self.hdf5lib_dest)
        
    def run(self):
        for cmd_name in self.get_sub_commands():
            self.run_command(cmd_name)
        self.announce("Fetching libhdf5 source")
        request = requests.get(self.url, stream=False)
        hdf5_zip = zipfile.ZipFile(StringIO.StringIO(request.content))
        hdf5_zip.extractall(self.hdf5lib_dest)
        
    def has_zlib_dependencies(self):
        return not os.path.exists(self.zlib_path)
    
    def has_szip_dependencies(self):
        return not os.path.exists(self.szip_path)
    
    sub_commands = setuptools.Command.sub_commands + [
        (FetchSZipSource.command_name, has_szip_dependencies),
        (FetchZlibSource.command_name, has_zlib_dependencies)]
        
class BuildIlastik(distutils.command.build.build):
    command_name = 'build'
    def has_source(self):
        return not os.path.exists(
            os.path.join(self.build_lib, "libhdf5"))
    
    sub_commands = distutils.command.build.build.sub_commands + \
        [(FetchLibHDF5Source.command_name, has_source),
         ('build_zlib', None)]

try:
    command_classes = dict([(cls.command_name, cls) for cls in (
            BuildIlastik, FetchLibHDF5Source, FetchSZipSource,
            FetchZlibSource)])
    command_classes['build_zlib'] = BuildWithCMake
    setuptools.setup(
        cmdclass=command_classes,
        options = {
            'build_zlib': dict(
                src_command=FetchZlibSource.command_name,
                extra_cmake_options = ["-DBUILD_SHARED_LIBS:BOOL=\"1\""])
            }
    )
except:
    import traceback
    traceback.print_exc()