#!/usr/bin/env python3

import sys

args = ' '.join(sys.argv)
if args == '-Wa,-v -c -o /dev/null -x assembler /dev/null':
    print('GNU assembler version 2.23.52.0.1 (x86_64-redhat-linux) using BFD version version 2.23.52.0.1-30.el7_1.2 20130226')
else:
    subprocess.check_call(['gcc'] + sys.argv)
