import os, sys, platform, subprocess, shutil, zipfile

lib_posix_mapping = {
    'debug': 'win64_vc150d',
    'release': 'win64_vc150r'
} 

config_mapping = {
    'debug': 'Debug',
    'release': 'Release'
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

def build_win64(src):
    build_x64('debug', src)
    build_x64('release', src)


curdir = os.path.dirname(os.path.abspath(__file__))
build_win64(curdir)