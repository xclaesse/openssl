#!/usr/bin/env python3

import os
import subprocess
import json
import shutil
import textwrap

from pathlib import Path

ASM_ARCHS = [
    # aix is not supported by Meson
    #'aix-gcc',
    #'aix64-gcc',
    'BSD-x86',
    'BSD-x86_64',
    'darwin64-x86_64-cc',
    'darwin-i386-cc',
    'darwin64-arm64-cc',
    'linux-aarch64',
    'linux-armv4',
    'linux-elf',
    'linux-x32',
    'linux-x86_64',
    'linux-ppc',
    'linux-ppc64',
    'linux-ppc64le',
    'linux32-s390x',
    'linux64-s390x',
    'linux64-mips64',
    'solaris-x86-gcc',
    'solaris64-x86_64-gcc',
    'VC-WIN64A',
    'VC-WIN32',
]

NO_ASM_ARCHS = [
    'VC-WIN64-ARM',
]

COPTS = ['no-comp', 'no-shared', 'no-afalgeng', 'enable-ssl-trace']

MESON_BUILD_TMPL = textwrap.dedent('''\
    # OpenSSL library
    libcrypto_sources = [
      {libcrypto_srcs}
    ]

    libssl_sources = [
      {libssl_srcs}
    ]

    openssl_defines = [
      {defines}
    ]

    openssl_cflags = [
      {cflags}
    ]

    openssl_libraries = [
      {libs}
    ]

    openssl_include_directories = [
      'generated-config/archs/{arch}/{asm}',
      'generated-config/archs/{arch}/{asm}/include',
      'generated-config/archs/{arch}/{asm}/crypto',
      'generated-config/archs/{arch}/{asm}/crypto/include/internal',
    ]

    # OpenSSL CLI
    openssl_cli_sources = [
      {apps_openssl_srcs}
    ]''')


def gen_arch(arch, asm):
    # Windows archs can only be generated on Windows with nmake instead of GNU make.
    is_win = arch.startswith('VC-WIN')
    make = 'nmake' if is_win else 'make'
    if not shutil.which(make):
        return

    # Configure OpenSSL for this arch
    cmd = ['perl', 'Configure'] + COPTS + [arch]
    env = os.environ.copy()
    env['CONFIGURE_CHECKER_WARN'] = '1'
    env['CC'] = 'gcc'
    if asm == 'no-asm':
        cmd.append(asm)
    elif asm == 'asm_avx2':
        env['CC'] = 'fake_gcc.py'
    subprocess.check_call(cmd, env=env)

    # Generate arch dependent header files
    subprocess.check_call([make, 'build_generated', 'crypto/buildinf.h', 'apps/progs.h'])
    base_dir = Path('generated-config', 'archs', arch, asm)
    Path(base_dir, 'crypto/include/internal').mkdir(parents=True, exist_ok=True)
    Path(base_dir, 'include/openssl').mkdir(parents=True, exist_ok=True)
    shutil.copy('include/openssl/opensslconf.h', base_dir / 'include/openssl')
    shutil.copy('include/crypto/bn_conf.h', base_dir / 'crypto/include/internal')
    shutil.copy('include/crypto/dso_conf.h', base_dir / 'crypto/include/internal')
    #shutil.copy('crypto/buildinf.h', base_dir / 'crypto')
    shutil.copy('apps/progs.h', base_dir / 'include')

    # Convert configdata.pm to json, then load it in python
    def configdata(varname):
        code = f'print encode_json(\%{varname});'
        stdout = subprocess.check_output(['perl', '-MJSON', '-I.', '-Mconfigdata', '-e', code])
        return json.loads(stdout)
    unified_info = configdata('unified_info')
    config = configdata('config')
    target = configdata('target')

    def get_sources(target):
        sources = []
        for obj in unified_info['sources'][target]:
            sources.append(unified_info['sources'][obj][0])
        return sources
    libssl_srcs = get_sources('libssl')
    libapps_srcs = get_sources(os.path.join('apps', 'libapps.a'))
    apps_openssl_srcs = get_sources(os.path.join('apps', 'openssl'))
    libcrypto_srcs = []
    generated_srcs = []
    for src in get_sources('libcrypto'):
        if src in unified_info['generate']:
            if is_win and src[-2:] in {'.s', '.S'}:
                src = src[:-2] + '.asm'
            generated_srcs.append(src)
        else:
            libcrypto_srcs.append(src)

    # Generate all sources
    env = os.environ.copy()
    env['CC'] = 'gcc'
    env['ASM'] = 'nasm'
    for src in generated_srcs:
        subprocess.check_call([make, src], env=env)
        dest = base_dir / Path(src).parent
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, dest)

    # Generate meson.build file
    prefix = f'generated-config/archs/{arch}/{asm}/'
    generated_srcs = [prefix + src for src in generated_srcs]

    lib_cppflags = target['lib_cppflags'].split()
    lib_cppflags = [i.replace('-D', '') for i in lib_cppflags]
    defines = config['defines'] + lib_cppflags + config['lib_defines'] + target['defines']

    cflags = []
    if not is_win:
        for i in config['cflags']:
            cflags += i.split()
        for i in config['CFLAGS']:
            cflags += i.split()
        cflags += target['cflags'].split()
        cflags += target['CFLAGS'].split()

    libs = []
    if 'ex_libs' in target:
        for lib in target['ex_libs'].split():
            if lib.startswith('-l'):
                lib = lib[2:]
            if lib.endswith('.lib'):
                lib = lib[:4]
            libs.append(lib)

    def join_strings(strv, exclude={}, paths=False):
        if paths and is_win:
            strv = [Path(i).as_posix() for i in strv]
        return ',\n  '.join([f'{i!r}' for i in strv if i not in exclude])
    d = {
        'libcrypto_srcs': join_strings(libcrypto_srcs + generated_srcs, paths=True),
        'libssl_srcs': join_strings(libssl_srcs, paths=True),
        'apps_openssl_srcs': join_strings(apps_openssl_srcs + libapps_srcs, paths=True),
        'defines': join_strings(defines, exclude={'NDEBUG'}),
        'cflags': join_strings(cflags, exclude={'-Wall', '-O3', '-pthread'}),
        'libs': join_strings(libs, exclude={'-pthread'}),
        'arch': arch,
        'asm': asm,
    }
    with Path(base_dir, 'meson.build').open('w', encoding='utf-8') as f:
        f.write(MESON_BUILD_TMPL.format(**d))

    # Cleanup
    subprocess.check_call([make, 'clean'])
    subprocess.check_call([make, 'distclean'])
    subprocess.check_call(['git', 'clean', '-f', 'crypto'])

for arch in ASM_ARCHS:
    gen_arch(arch, 'asm')
    gen_arch(arch, 'asm_avx2')
    gen_arch(arch, 'no-asm')

for arch in NO_ASM_ARCHS:
    gen_arch(arch, 'no-asm')

