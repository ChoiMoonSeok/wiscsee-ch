#!/usr/bin/env python

import argparse
import sys

import config
import dmftl
import flash
import recorder


def event_line_to_dic(line):
    keys = ['operation', 'offset', 'size']
    items = line.strip('\n').split()
    items[1] = eval(items[1]) # offset
    items[2] = eval(items[2]) # size
    event = dict(zip(keys, items))
    return event

class Simulator(object):
    def __init__(self, conf):
        "conf is class Config"
        self.conf = conf

        # initialize recorder
        self.rec = recorder.Recorder(output_target = self.conf['output_target'],
            path = self.conf.get_output_file_path(),
            verbose_level = self.conf['verbose_level'])

        if self.conf['ftl_type'] == 'directmap':
            self.ftl = dmftl.DmFtl(self.conf, self.rec,
                flash.Flash(recorder = self.rec))
        else:
            raise ValueError("ftl_type {} is not defined"\
                .format(self.conf['ftl_type']))


    def process_event(self, event):
        pages = self.conf.off_size_to_page_list(event['offset'], event['size'])

        if event['operation'] == 'read':
            for page in pages:
                self.ftl.lba_read(page)
        elif event['operation'] == 'write':
            for page in pages:
                self.ftl.lba_write(page)
        elif event['operation'] == 'discard':
            for page in pages:
                self.ftl.lba_discard(page)

    def run(self, event_line_iter):
        # This should be the only place that we load config
        # do this before running the simulator, Not when initializing the
        # simulator class
        # config.conf.load_from_dict(confdic)

        # you have to load configuration first before initialize recorder
        # recorder.initialize()
        for event_line in event_line_iter:
            event = event_line_to_dic(event_line)
            self.process_event(event)
