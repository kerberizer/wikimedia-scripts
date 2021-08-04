#!/usr/bin/env python3

import sys


def main(argv):
    if len(argv) < 2:
        print('Error: Please provide a filename.', file=sys.stderr)
        sys.exit(1)
    fname = argv.pop()
    with open(fname) as f:
        site_list = [_.rstrip().removeprefix('* ').lower() for _ in f]
    site_list.sort()
    filter_src = '& ('
    site_index = ''
    regex_list = []
    for site in site_list:
        if site[0] != site_index.lower() and site_index != 'NON_ASCII':
            if site_index != '':
                filter_src += "\t')';\n"
            if ord(site[0]) < 128:
                site_index = site[0].capitalize()
            elif site_index != 'NON_ASCII':
                site_index = 'NON_ASCII'
            regex_name = f'regex_{site_index}'
            filter_src += f"\n\t{regex_name}:='('+\n"
            regex_list.append(regex_name)
        else:
            filter_src += "\t\t'|'+\n"
        filter_src += "\t\t'\\b{}\\b'+\n".format(site.replace('.', '\\.'))
    filter_src += "\t')';\n"
    for regex in regex_list:
        filter_src += f'\n\tadded_links irlike {regex} |'
    print(filter_src[:-2])
    print(')')


if __name__ == '__main__':
    main(sys.argv)

# vim: set ts=4 sts=4 sw=4 tw=100 et:
