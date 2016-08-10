#!/usr/bin/env python
import abc
import argparse
import random
import simpy
import sys
import os
import csv
import pprint

import bmftl
import config
import ssdframework
import dftlext
import dmftl
import flash
import nkftl2
import recorder
import hostevent
import dftldes

from commons import *
from ftlsim_commons import *
from .host import Host
from utilities import utils

import prepare4pyreuse
from pyreuse.sysutils import blocktrace, blockclassifiers, dumpe2fsparser
from pyreuse.fsutils import ext4dumpextents

class Simulator(object):
    __metaclass__ = abc.ABCMeta

    @abc.abstractmethod
    def run(self):
        return

    @abc.abstractmethod
    def get_sim_type(self):
        return

    def __init__(self, conf, event_iter):
        "conf is class Config"
        if not isinstance(conf, config.Config):
            raise TypeError("conf is not config.Config, it is {}".
                format(type(conf).__name__))

        self.conf = conf
        self.event_iter = event_iter

        # initialize recorder
        self.recorder = recorder.Recorder(output_target = self.conf['output_target'],
            output_directory = self.conf['result_dir'],
            verbose_level = self.conf['verbose_level'],
            print_when_finished = self.conf['print_when_finished']
            )

        if self.conf.has_key('enable_e2e_test'):
            raise RuntimeError("enable_e2e_test is deprecated")


class SimulatorDESNew(Simulator):
    def __init__(self, conf, event_iter):
        super(SimulatorDESNew, self).__init__(conf, event_iter)

        self.env = simpy.Environment()
        self.host = Host(self.conf, self.env, event_iter)
        self.ssd = ssdframework.Ssd(self.conf, self.env,
                self.host.get_ncq(), self.recorder)

    def run(self):
        self.env.process(self.host.run())
        self.env.process(self.ssd.run())

        self.env.run()

        self.record_post_run_stats()

    def get_sim_type(self):
        return "SimulatorDESNew"

    def record_post_run_stats(self):
        self.recorder.set_result_by_one_key(
                'simulation_duration', self.env.now)
        pprint.pprint(self.recorder.get_result_summary())

        self.recorder.close()

        gclog = GcLog(device_path=self.conf['device_path'],
                result_dir=self.conf['result_dir'],
                flash_page_size=self.conf.page_size)
        if self.conf['filesystem'] == 'ext4' and \
                os.path.exists(gclog.gclog_path) and \
                os.path.exists(gclog.extents_path):
            gclog.classify_lpn_in_gclog()


class GcLog(object):
    def __init__(self, device_path, result_dir, flash_page_size):
        self.device_path = device_path
        self.result_dir = result_dir
        self.flash_page_size = flash_page_size

        self.gclog_path = os.path.join(self.result_dir, 'gc.log')
        self.dumpe2fs_out_path = os.path.join(self.result_dir, 'dumpe2fs.out')
        self.extents_path = os.path.join(self.result_dir, 'extents.json')
        self.fs_block_size = 4096

    def classify_lpn_in_gclog(self):
        extents = self._get_extents()
        filepath_classifier = blockclassifiers.Ext4FileClassifier(extents,
                self.fs_block_size)

        range_table = self._get_range_table()
        classifier = blockclassifiers.Ext4BlockClassifier(range_table,
                self.fs_block_size)

        new_table = []
        with open(self.gclog_path , 'rb') as f:
            reader = csv.DictReader(f, skipinitialspace=True)
            for row in reader:
                newrow = dict(zip(row.keys()[0].split(), row.values()[0].split()))
                offset = int(newrow['lpn']) * self.flash_page_size
                sem = classifier.classify(offset)
                if sem == 'UNKNOWN':
                    sem = filepath_classifier.classify(offset)
                newrow['semantics'] = sem
                new_table.append(newrow)

        with open(self.gclog_path+'.parsed', 'w') as f:
            f.write(utils.table_to_str(new_table))

    def _get_extents(self):
        d = utils.load_json(self.extents_path)
        extents = d['extents']

        return extents

    def _get_range_table(self):
        with open(self.dumpe2fs_out_path, 'r') as f:
            text = f.read()

        header_text, bg_text = text.split("\n\n\n")

        range_table = dumpe2fsparser.parse_bg_text(bg_text)

        j_start, j_end = self._get_journal_block_ext(header_text)
        if j_start != -1:
            range_table.append( {'journal': (j_start, j_end)} )

        return range_table

    def _get_journal_block_ext(self, header_text):
        header_dict = dumpe2fsparser.parse_header_text(header_text)

        if header_dict.has_key('journal-inode') is not True:
            return -1, -1

        journal_inum = header_dict['journal-inode']
        journal_len = header_dict['journal-length']

        ext_text = ext4dumpextents.dump_extents_of_a_file(self.device_path,
                '<{}>'.format(journal_inum))
        table = ext4dumpextents.parse_dump_extents_output(ext_text)
        return table[0]['Physical_start'], table[0]['Physical_end']


def create_simulator(simulator_class, conf, event_iter):
    cls = eval(simulator_class)
    return cls(conf, event_iter)




def random_data(addr):
    randnum = random.randint(0, 10000)
    content = "{}.{}".format(addr, randnum)
    return content


class SimulatorNonDES(Simulator):
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf, event_iter):
        super(SimulatorNonDES, self).__init__(conf, event_iter)

        if self.conf['ftl_type'] == 'dftlext':
            ftl_class = dftlext.Dftl
        elif self.conf['ftl_type'] == 'nkftl2':
            ftl_class = nkftl2.Ftl
        else:
            raise ValueError("ftl_type {} is not defined"\
                .format(self.conf['ftl_type']))

        self.ftl = ftl_class(self.conf, self.recorder,
            flash.Flash(recorder = self.recorder, confobj = self.conf))

    def run(self):
        """
        You must garantee that each item in event_iter is a class Event
        """
        cnt = 0
        for event in self.event_iter:
            self.process_event(event)
            cnt += 1
            if cnt % 100 == 0:
                print '|',
                sys.stdout.flush()

        self.ftl.post_processing()

    def process_event(self, event):
        if event.operation == OP_READ:
            self.read(event)
        elif event.operation == OP_WRITE:
            self.write(event)
        elif event.operation == OP_DISCARD:
            self.discard(event)
        elif event.operation == OP_ENABLE_RECORDER:
            self.ftl.enable_recording()
        elif event.operation == OP_DISABLE_RECORDER:
            self.ftl.disable_recording()
        elif event.operation == OP_WORKLOADSTART:
            self.ftl.pre_workload()
        elif event.operation in ['finish', OP_BARRIER, OP_REC_TIMESTAMP, OP_CLEAN]:
            # ignore this
            pass
        else:
            print event
            raise RuntimeError("operation '{}' is not supported".format(
                event.operation))


class SimulatorNonDESSpeed(SimulatorNonDES):
    """
    This one does not do e2e test
    It uses extents
    """
    def get_sim_type(self):
        return "NonDESSpeed"

    def write(self, event):
        self.ftl.sec_write(
                sector = event.sector,
                count = event.sector_count,
                data = None)

    def read(self, event):
        """
        read extent from flash and check if the data is correct.
        """
        self.ftl.sec_read(
                sector = event.sector,
                count = event.sector_count)

    def discard(self, event):
        self.ftl.sec_discard(
            sector = event.sector,
            count = event.sector_count)


class SimulatorNonDESe2eExtent(SimulatorNonDES):
    """
    This one does not do e2e test
    It uses extents
    """
    def __init__(self, conf, event_iter):
        super(SimulatorNonDESe2eExtent, self).__init__(conf, event_iter)

        self.lpn_to_data = {}

    def get_sim_type(self):
        return "NonDESe2eExtent"

    def write(self, event):
        """
        1. Generate random data
        2. Copy random data to lsn_to_data
        3. Write data by ftl
        """
        extent = event.get_lpn_extent(self.conf)

        data = []
        for lpn in extent.lpn_iter():
            content = random_data(lpn)
            self.lpn_to_data[lpn] = content
            data.append(content)

        self.ftl.write_ext(extent, data = data)

    def read(self, event):
        """
        read extent from flash and check if the data is correct.
        """
        extent = event.get_lpn_extent(self.conf)
        data = self.ftl.read_ext(extent)

        self.check_read(event, data)

    def check_read(self, event, data):
        extent = event.get_lpn_extent(self.conf)
        for lpn, lpn_data in zip(extent.lpn_iter(), data):
            if self.lpn_to_data.get(lpn, None) != lpn_data:
                msg = "Data is not correct. Got: {read}, "\
                        "Correct: {correct}. lpn={lpn}".format(
                        read=lpn_data,
                        correct=self.lpn_to_data.get(lpn, None),
                        lpn=lpn)
                print msg

    def discard(self, event):
        extent = event.get_lpn_extent(self.conf)
        self.ftl.discard_ext(extent)

        for lpn in extent.lpn_iter():
            try:
                del self.lpn_to_data[lpn]
            except KeyError:
                pass


class SimulatorNonDESe2e(SimulatorNonDES):
    """
    This one does not do e2e test
    It uses extents
    """
    def __init__(self, conf, event_iter):
        super(SimulatorNonDESe2e, self).__init__(conf, event_iter)

        self.lsn_to_data = {}

    def get_sim_type(self):
        return "NonDESe2e"

    def write(self, event):
        """
        1. Generate random data
        2. Copy random data to lsn_to_data
        3. Write data by ftl
        """
        data = []
        for sec in range(event.sector, event.sector + event.sector_count):
            content = random_data(sec)
            self.lsn_to_data[sec] = content
            data.append(content)

        self.ftl.sec_write(
                sector = event.sector,
                count = event.sector_count,
                data = data)

    def read(self, event):
        """
        read extent from flash and check if the data is correct.
        """
        data = self.ftl.sec_read(
                sector = event.sector,
                count = event.sector_count)

        self.check_read(event, data)

    def check_read(self, event, data):
        for sec, sec_data in zip(
                range(event.sector, event.sector + event.sector_count), data):
            if self.lsn_to_data.get(sec, None) != sec_data:
                msg = "Data is not correct. Got: {read}, "\
                        "Correct: {correct}. sector={sec}".format(
                        read = sec_data,
                        correct = self.lsn_to_data.get(sec, None),
                        sec = sec)
                print msg
                # raise RuntimeError(msg)

    def discard(self, event):
        self.ftl.sec_discard(
            sector = event.sector,
            count = event.sector_count)

        for sec in range(event.sector, event.sector + event.sector_count):
            try:
                del self.lsn_to_data[sec]
            except KeyError:
                pass


class SimulatorDESSync(Simulator):
    def __init__(self, conf, event_iters):
        """
        event_iters is list of event iterators
        """
        super(SimulatorDESSync, self).__init__(conf, None)

        if not isinstance(event_iters, list):
            raise RuntimeError("event_iters must be a list of iterators.")

        self.event_iters = event_iters

        self.env = simpy.Environment()
        self.ssdframework = ssdframework.SSDFramework(self.conf, self.recorder, self.env)

    def host_proc(self, pid, event_iter):
        """
        This process acts like a producer, putting requests to ncq
        """

        # this token is acquired before we issue request to queue.
        # it effectively control the queue depth of this process
        token = simpy.Resource(self.env,
                capacity = self.conf['process_queue_depth'])

        for event in event_iter:
            event.token = token
            event.token_req = event.token.request()

            yield event.token_req

            yield self.ssdframework.ncq.queue.put(event)

        for i in range(self.conf['SSDFramework']['ncq_depth']):
            event = hostevent.ControlEvent(OP_SHUT_SSD)

            event.token = token
            event.token_req = event.token.request()

            yield event.token_req

            yield self.ssdframework.ncq.queue.put(event)

    def run(self):
        for i, event_iter in enumerate(self.event_iters):
            self.env.process(self.host_proc(i, event_iter))
        self.env.process(self.ssdframework.run())

        self.env.run()

    def get_sim_type(self):
        return "SimulatorDES"

    def write(self):
        raise NotImplementedError()

    def read(self):
        raise NotImplementedError()

    def discard(self):
        raise NotImplementedError()

class SimulatorDESTime(Simulator):
    def __init__(self, conf, event_iter):
        super(SimulatorDESTime, self).__init__(conf, None)


        self.event_iter = event_iter

        self.env = simpy.Environment()
        self.ssdframework = ssdframework.SSDFramework(self.conf, self.recorder,
                self.env)

    def host_proc(self, event_iter):
        """
        This process acts like a producer, putting requests to ncq
        """

        # this token is acquired before we issue request to queue.
        # it effectively control the queue depth of this process
        token = simpy.Resource(self.env,
                capacity = self.conf['process_queue_depth'])

        for event in event_iter:
            event.token = token
            event.token_req = event.token.request()

            if self.conf['simulator_enable_interval'] == True:
                if event.operation in (OP_READ, OP_WRITE, OP_DISCARD):
                    yield self.env.timeout(int(event.pre_wait_time * SEC))

            yield self.ssdframework.ncq.queue.put(event)

        for i in range(self.conf['SSDFramework']['ncq_depth']):
            event = hostevent.ControlEvent(OP_SHUT_SSD)

            event.token = token
            event.token_req = event.token.request()

            yield event.token_req

            yield self.ssdframework.ncq.queue.put(event)

        print 'Host finish time', self.env.now

    def run(self):
        self.env.process(self.host_proc(self.event_iter))
        self.env.process(self.ssdframework.run())

        self.env.run()

    def get_sim_type(self):
        return "SimulatorDESTime"

    def write(self):
        raise NotImplementedError()

    def read(self):
        raise NotImplementedError()

    def discard(self):
        raise NotImplementedError()


