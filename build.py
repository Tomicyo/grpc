import hashlib
import logging
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib
import urllib2
import zipfile


max_thread = 10
logging.basicConfig(level=logging.INFO)

class _RangeContentDownloader(threading.Thread):

    def __init__(self, id, url, start, end, part_file_name, file_lock):
        self.id = id
        self.url = url
        self.start = start
        self.end = end
        self.total_size = int(end) - int(start)
        self.part_path = part_file_name
        self.file = open(part_file_name, 'wb')
        self.written_size = 0
        self.session = None
        self.retry_setup_no = 0
        self.retry_receive_no = 0
        self.memcache_size = 1024 * 1024 * 4
        self.current_data = None

    def start_download(self):
        ret = self._setup_request()
        if ret > 0:
            while ret < 5 and ret != 0:
                ret = self._setup_request()
            if not self.session:
                logging.warning('chunk%d failed to setup session, after retry 5 times !', self.id)
        logging.info('start transfering chunk %d', self.id)
        ret = self._receive()
        if ret > 0:
            while ret < 5 and ret != 0:
                ret = self._receive()
        self.file.close()

    def _setup_request(self):
        try:
            req = urllib2.Request(self.url)
            req.headers['Range'] = 'bytes=%d-%d' % (self.start, self.end)
            self.session = urllib2.urlopen(req)
            return 0
        except Exception, e:
            self.retry_setup_no = self.retry_setup_no + 1
            logging.warning('chunk%d: _setup_request: retry time: %d', self.id, self.retry_setup_no)
            return self.retry_receive_no

    def _receive(self):
        try:
            buf_sz = 1024 * 4
            data_piece = self.session.read(buf_sz)
            pending_data = data_piece
            while data_piece:
                data_piece = self.session.read(buf_sz)
                if len(data_piece) < buf_sz:
                    # finish transfering
                    if data_piece:
                        pending_data = pending_data + data_piece
                    self._sendto_filestream(pending_data, len(pending_data))
                    logging.info('chunk%d: >>>> finish transfering section %d-%d', self.id, self.start, self.end)
                    break
                if not pending_data:
                    pending_data = data_piece
                else:
                    pending_data = pending_data + data_piece
                buf_sz = buf_sz * 2
                if len(pending_data) > self.memcache_size:
                    logging.info('pending data size larger than memcache size, need to write to file, size %d', len(pending_data))
                    self._sendto_filestream(pending_data, len(pending_data))
                    pending_data = None
            return 0
        except Exception, e:
            self.retry_receive_no = self.retry_receive_no + 1
            logging.warning('chunk%d: _receive: retry time: %d', self.id, self.retry_receive_no)
            return self.retry_receive_no

    def _sendto_filestream(self, data, offset):
        self.file.write(data)
        self.file.flush()
            
class Downloader(threading.Thread):
    def __init__(self, url):
        self.url = url
        self.threadNum = 16
        self.lock = threading.RLock()
        self.part_jobs = []
        threading.Thread.__init__(self)


    def getFilename(self):
        url = self.url
        protocol, s1 = urllib.splittype(url)
        host, path = urllib.splithost(s1)
        filename = path.split('/')[-1]
        if '.' not in filename:
            filename = None
        print "Do you want to change a filename?('y' or other words)"
        answer = raw_input()
        if answer == "y" or filename is None:
            print "Please input your new filename:"
            filename = raw_input()
        return filename

    def getLength(self):
        opener = urllib2.build_opener()
        req = opener.open(self.url)
        meta = req.info()
        length = int(meta.getheaders("Content-Length")[0])
        return length

    def get_range(self):
        ranges = []
        length = self.getLength()
        logging.info('Url: %s\n\tLength: %d MB' % (self.url, length / 1024 / 1024))
        offset = int(int(length) / self.threadNum)
        for i in range(self.threadNum):
            if i == (self.threadNum - 1):
                ranges.append((i*offset, length))
            else:
                ranges.append((i*offset,(i+1)*offset))
        return ranges

    def downloadThread(self, start, end, thread_id):
        part_file_path = os.path.join(tempfile.gettempdir(), '%s_part_%d' % (self.simple_file_name, thread_id))
        range_content = _RangeContentDownloader(thread_id, self.url, start, end, part_file_path, self.lock)
        self.part_jobs.append(range_content)
        range_content.start_download()

    def download(self, fileName=None, simple_name=None):
        filename = fileName if fileName else self.getFilename()
        self.simple_file_name = simple_name
        thread_list = []
        n = 0
        self.time_begin = time.time()
        start_offsets = []
        for ran in self.get_range():
            start, end = ran
            n += 1
            start_offsets.append(start)
            thread = threading.Thread(target=self.downloadThread,args=(start, end, n))
            thread_list.append(thread)
            thread.start()

        for i in thread_list:
             i.join()
        
        logging.info('finish download %s, next join the parts together', self.url)

        self.file = open(filename, 'wb')
        for i in range(self.threadNum):
            path = os.path.join(tempfile.gettempdir(), '%s_part_%d' % (simple_name, i+1))
            #print path
            with open(path, 'rb') as p_file:
                self.file.seek(start_offsets[i])
                data = p_file.read(1024*1024*4)
                self.file.write(data)
                self.file.flush()
                while data:
                    data = p_file.read(1024*1024*4)
                    self.file.write(data)
                    self.file.flush()
                p_file.close()
            os.remove(path)
        self.file.close()

def download_and_extract(url, extract_dir):
    h = hashlib.md5()
    h.update(url)
    filename = str(h.hexdigest())
    down = Downloader(url)
    dfile_path = os.path.join(tempfile.gettempdir(), filename + '.zip')
    print dfile_path
    down.download(dfile_path, filename)
    zipFile = zipfile.ZipFile(dfile_path)
    zipFile.extractall(extract_dir)
    zipFile.close()


def copy_files_by_ext(file_dir, file_ext, target_dir):
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    for _file in os.listdir(file_dir):
        if _file.endswith(file_ext):
            shutil.copyfile(os.path.join(file_dir, _file), os.path.join(target_dir, _file))

def copy_files(file_dir, target_dir):
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    for _file in os.listdir(file_dir):
        real_path = os.path.join(file_dir, _file)
        target_path = os.path.join(target_dir, _file)
        if os.path.isdir(real_path):
            copy_files(real_path, target_path)
        elif os.path.isfile(real_path):
            shutil.copyfile(real_path, target_path)
            
def zipdir(path, ziph):
    for root, dirs, files in os.walk(path):
        for _file in files:
            ziph.write(os.path.join(root, _file), os.path.join(root[len(path)+1:], _file))

def gen_static_lib_target(name, lib_path_prefix, conf):
    cmake_src = '''
add_library({&target} STATIC IMPORTED)
set_property(TARGET {&target} APPEND PROPERTY IMPORTED_CONFIGURATIONS DEBUG)
set_property(TARGET {&target} APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties({&target} PROPERTIES 
    IMPORTED_LOCATION_DEBUG "${install_prefix}/lib/{&target_debug_lib}"
    IMPORTED_LOCATION_RELEASE "${install_prefix}/lib/{&target_release_lib}"
#    IMPORTED_LINK_INTERFACE_LANGUAGES_DEBUG "CXX"
    INTERFACE_INCLUDE_DIRECTORIES "${install_prefix}/include")
    '''.replace('{&target}', name).replace('{&target_debug_lib}', conf['debug']).replace('{&target_release_lib}', conf['release'])
    return cmake_src

def gen_shared_lib_target(name, lib_path_prefix, conf):
    return '''
add_library({&target} SHARED IMPORTED)
set_property(TARGET {&target} APPEND PROPERTY IMPORTED_CONFIGURATIONS DEBUG)
set_property(TARGET {&target} APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
set_target_properties({&target} PROPERTIES 
    IMPORTED_LOCATION_DEBUG "${install_prefix}/bin/{&target_debug_so}"
    IMPORTED_IMPLIB_DEBUG "${install_prefix}/lib/{&target_debug_lib}"
    IMPORTED_LOCATION_RELEASE "${install_prefix}/bin/{&target_release_so}"
    IMPORTED_IMPLIB_RELEASE "${install_prefix}/lib/{&target_release_lib}"
    INTERFACE_INCLUDE_DIRECTORIES "${install_prefix}/include"
)
    '''.replace('{&target}', name).replace('{&target_debug_lib}', conf['debug']).replace('{&target_release_lib}', conf['release']) \
        .replace('{&target_debug_so}', conf['debugso']) \
        .replace('{&target_release_so}', conf['releaseso'])

def gen_host_exe_target(name, bin_path_prefix, conf):
    return '''
    add_executable({&target} IMPORTED)
    set_property(TARGET {&target} APPEND PROPERTY IMPORTED_CONFIGURATIONS RELEASE)
    set_target_properties({&target} PROPERTIES IMPORTED_LOCATION_RELEASE "${install_prefix}/bin/{&target_exe}")
'''.replace('{&target}', name).replace('{&target_exe}', conf['exe'])

target_type_action = {
    'shared_lib': gen_shared_lib_target,
    'static_lib': gen_static_lib_target,
    'program':    gen_host_exe_target,
}


def gen_cmake_package(path, pk_configs):
    cpf = open(path, 'w')
    cpf.write('get_filename_component(install_prefix "${CMAKE_CURRENT_LIST_DIR}" ABSOLUTE)\n')

    for item in pk_configs:
        item_name = item['name']
        item_type = item['type']
        item_conf = item['win64']
        cpf.write('if(WIN32)\n')
        cpf.write(target_type_action[item_type](item_name, 'lib', item_conf))
        cpf.write('\nendif(WIN32) # \n')
    cpf.close()

lib_posix_mapping = {
    'debug': 'win64_vc150d',
    'release': 'win64_vc150r'
} 

config_mapping = {
    'debug': 'Debug',
    'release': 'Release'
}

urls = {
    'protobuf': 'https://ci.appveyor.com/api/buildjobs/ic77nh5lpobvkmxw/artifacts/output%2Fprotobuf_md_windows.zip',
    '3rdparty': 'https://ci.appveyor.com/api/projects/tomicyo/third-party-clibs/artifacts/build/third_party_clibs_windows.zip'
}

def build_x64(config, src):
    bld = os.path.join(src, '.build', 'win64', config)
    ins = os.path.join(src, '.build', 'artifacts', 'win64_' + config)
    
    P = subprocess.Popen(['cmake', 
        '-GVisual Studio 15 2017 Win64',
        '-H{0}'.format(src),
        '-B{0}'.format(bld),
        '-DCMAKE_BUILD_TYPE={0}'.format(config_mapping[config]),
        '-DCMAKE_INSTALL_PREFIX={0}'.format(ins),
        '-DgRPC_BUILD_TESTS=OFF',
        '-DBENCHMARK_ENABLE_TESTING=OFF',
        '-Dprotobuf_BUILD_TESTS=OFF',
        '-Dprotobuf_BUILD_EXAMPLES=OFF',
        ])
    P.wait()

    B = subprocess.Popen(['cmake',
        '--build', bld,
        '--config', config_mapping[config]
        ])
    B.wait()

def build_mini_x64_with_k3d(config, src):
    bld = os.path.join(src, '.build', 'win64_mini', config)
    ins = os.path.join(src, '.build', 'artifacts_mini', 'win64_' + config)
    dep = os.path.join(src, '.build', 'dependency')

    dep_inc = os.path.join(dep, 'include').replace('\\', '/')
    protoc = os.path.join(dep, 'bin', 'win64_vc150r', 'protoc.exe').replace('\\', '/')

    P = subprocess.Popen(['cmake', 
        '-GVisual Studio 15 2017 Win64',
        '-H{0}'.format(src),
        '-B{0}'.format(bld),
        '-DCMAKE_BUILD_TYPE={0}'.format(config_mapping[config]),
        '-DCMAKE_INSTALL_PREFIX={0}'.format(ins),
        '-DgRPC_INSTALL=TRUE',
        '-DgRPC_BUILD_TESTS=OFF',
        '-DBENCHMARK_ENABLE_TESTING=OFF',
        '-Dprotobuf_BUILD_TESTS=OFF',
        '-Dprotobuf_BUILD_EXAMPLES=OFF',
        #'-DgRPC_USE_PROTO_LITE=ON', # default to proto lite
        '-DgRPC_MSVC_STATIC_RUNTIME=OFF',
        '-DgRPC_PROTOBUF_PROVIDER=kaleido3d',
        '-DgRPC_SSL_PROVIDER=kaleido3d',
        '-DgRPC_ZLIB_PROVIDER=kaleido3d',
        '-DgRPC_CARES_PROVIDER=kaleido3d',
        '-DgRPC_PROVIDER=kaleido3d',
        '-D_gRPC_PROTOBUF_PROTOC_EXECUTABLE={0}'.format(protoc),
        #'-D_gRPC_PROTOBUF_PROTOC_LIBRARIES=',
        #'-D_gRPC_PROTOBUF_LIBRARIES=',
        '-D_gRPC_PROTOBUF_INCLUDE_DIR={0}'.format(dep_inc),
        #'-D_gRPC_ZLIB_LIBRARIES=',
        '-D_gRPC_ZLIB_INCLUDE_DIR={0}'.format(dep_inc),
        #'-D_gRPC_SSL_LIBRARIES=',
        '-D_gRPC_SSL_INCLUDE_DIR={0}'.format(dep_inc),
        '-D_gRPC_CARES_INCLUDE_DIR={0}'.format(dep_inc),
        ])
    P.wait()

    B = subprocess.Popen(['cmake',
        '--build', bld,
        '--config', config_mapping[config],
        '--target', 'install'
        ])
    B.wait()

pk_config = [
    {
        'name': 'gpr',
        'platforms': ['win64'],
        'type': 'static_lib',
        'win64': 
        {
            'debug':    'win64_vc150d/gpr.lib',
            'release':  'win64_vc150r/gpr.lib',
        }
    },
    {
        'name': 'grpc',
        'platforms': ['win64'],
        'type': 'static_lib',
        'win64': 
        {
            'debug':    'win64_vc150d/grpc.lib',
            'release':  'win64_vc150r/grpc.lib',
        }
    },
    {
        'name': 'grpc_cronet',
        'platforms': ['win64'],
        'type': 'static_lib',
        'win64': 
        {
            'debug':    'win64_vc150d/grpc_cronet.lib',
            'release':  'win64_vc150r/grpc_cronet.lib',
        }
    },
    {
        'name': 'grpc++',
        'platforms': ['win64'],
        'type': 'static_lib',
        'win64': 
        {
            'debug':    'win64_vc150d/grpc++.lib',
            'release':  'win64_vc150r/grpc++.lib',
        }
    },
    {
        'name': 'grpc_unsecure',
        'platforms': ['win64'],
        'type': 'static_lib',
        'win64': 
        {
            'debug':    'win64_vc150d/grpc_unsecure.lib',
            'release':  'win64_vc150r/grpc_unsecure.lib',
        }
    },
    {
        'name': 'grpc++_unsecure',
        'platforms': ['win64'],
        'type': 'static_lib',
        'win64': 
        {
            'debug':    'win64_vc150d/grpc++_unsecure.lib',
            'release':  'win64_vc150r/grpc++_unsecure.lib',
        }
    }
]

def build_win64(src):
    ins = os.path.join(src, '.build', 'artifacts_mini')
    artifacts_dir = os.path.join(src, 'output', '__tmp')
    real_dir = os.path.join(src, 'output')
    build_mini_x64_with_k3d('debug', src)
    build_mini_x64_with_k3d('release', src)
    #build_x64('release', src)
    copy_files(os.path.join(ins, 'win64_debug', 'include'), os.path.join(artifacts_dir, 'include'))
    copy_files_by_ext(os.path.join(ins, 'win64_debug', 'lib'), '.lib', os.path.join(artifacts_dir, 'lib', 'win64_vc150d'))
    copy_files_by_ext(os.path.join(ins, 'win64_release', 'lib'), '.lib', os.path.join(artifacts_dir, 'lib', 'win64_vc150r'))
    copy_files_by_ext(os.path.join(ins, 'win64_release', 'bin'), 'grpc_cpp_plugin.exe', os.path.join(artifacts_dir, 'bin', 'win64_vc150r'))
    gen_cmake_package(os.path.join(artifacts_dir, 'grpc.cmake'), pk_config)
    archive_name = os.path.join(real_dir, 'grpc_windows.zip')
    zipf = zipfile.ZipFile(archive_name, 'w', zipfile.ZIP_DEFLATED)
    zipdir(artifacts_dir, zipf)
    zipf.close()


curdir = os.path.dirname(os.path.abspath(__file__))
print curdir
download_and_extract(urls['protobuf'], os.path.join(curdir, '.build', 'dependency'))
download_and_extract(urls['3rdparty'], os.path.join(curdir, '.build', 'dependency'))
build_win64(curdir)
