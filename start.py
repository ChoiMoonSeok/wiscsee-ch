import unittest
import collections
import shutil
import os


from workflow import *
import wiscsim
from utilities import utils
from wiscsim.hostevent import Event, ControlEvent
from config_helper import rule_parameter
from pyreuse.helpers import shcmd
from config_helper import experiment

class TestLinuxDdGrouping(unittest.TestCase):
    def test(self):
        for para in rule_parameter.ParaDict(expname="linux-dd-grouping", 
                                            trace_expnames=['linux-dd-exp'],
                                            rule="grouping"):
            experiment.execute_simulation(para)

if __name__ == '__main__':
    unittest.main()