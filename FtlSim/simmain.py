#!/usr/bin/env python

import argparse
from common import byte_to_pagenum, off_size_to_page_list
import sys

import config
import ftl
import recorder

# TODO
# 1. read LBA layer trace
# 2. implement dmftl in ftl module
# 3. implement recorder.py


# finish the testing chain
# - LBA pattern workload generator
# - seq, random, stride
# - pattern visualizor
# - LBA
# - flash page

# do sanity check with the above

# - block mapping
# - hybrid mapping

def read_lba_events(fpath):
    with open(fpath, 'r') as f:
        events = f.readlines()
    keys = ['operation', 'offset', 'size']
    for i, event in enumerate(events):
        event = event.strip('\n').split()
        event[1] = eval(event[1]) # offset
        # for debug
        # event[1] = eval(event[1])%(4096*4*16) #
        event[2] = eval(event[2]) # size
        events[i] = event

    events = [dict(zip(keys, e)) for e in events]
    return events

def event_line_to_dic(line):
    keys = ['operation', 'offset', 'size']
    items = line.strip('\n').split()
    items[1] = eval(items[1]) # offset
    items[2] = eval(items[2]) # size
    event = dict(zip(keys, items))
    return event

def process_event(event):
    pages = off_size_to_page_list(event['offset'], event['size'])

    if event['operation'] == 'read':
        for page in pages:
            ftl.lba_read(page)
    elif event['operation'] == 'write':
        for page in pages:
            ftl.lba_write(page)
    elif event['operation'] == 'discard':
        for page in pages:
            ftl.lba_discard(page)

def sim_run(event_line_iter):
    cnt = 0
    for event_line in event_line_iter:
        event = event_line_to_dic(event_line)
        process_event(event)
        ftl.debug_after_processing()
        cnt += 1
        if cnt % 10 == 0:
            recorder.warning('currnt count', cnt)
            sys.stdout.flush()

def load_config_from_dict(dic):
    config.load_from_dict(dic)

def main():
    parser = argparse.ArgumentParser(
            description="It takes event input file."
            )
    parser.add_argument('-c', '--configfile', action='store',
        help='config file path (REQUIRED)')
    parser.add_argument('-e', '--events', action='store',
        help='event file (REQUIRED)')
    parser.add_argument('-v', '--verbose', action='store',
        help='verbose level: 0-minimum, 1-more')
    args = parser.parse_args()

    if args.events == None:
        parser.print_help()
        exit(1)

    if args.configfile == None:
        parser.print_help()
        exit(1)

    if args.verbose != None:
        config.verbose_level = int(args.verbose)

    config.load_from_json_file(args.configfile)
    # sim_run(open(args.events, 'r'))

if __name__ == '__main__':
    main()

