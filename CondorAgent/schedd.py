###### COPYRIGHT NOTICE ########################################################
#
# Copyright (C) 2007-2011, Cycle Computing, LLC.
# 
# Licensed under the Apache License, Version 2.0 (the "License"); you
# may not use this file except in compliance with the License.  You may
# obtain a copy of the License at
# 
#   http://www.apache.org/licenses/LICENSE-2.0.txt
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
################################################################################

################################################################################
# USAGE
################################################################################


################################################################################
# IMPORTS
################################################################################
import util
import time
import math
import logging
import os
import glob


################################################################################
# GLOBALS
################################################################################
__doc__ = "CondorAgent Utilities for Condor: Scheduler"

# the number of seconds to allow as overlap to make sure we don't miss any history,
# both when returning a CompletedSince indicator for future reads, as well as
# deciding what history files need to be processed
COMPLETED_SINCE_OVERLAP = 2


################################################################################
# CLASSES
################################################################################
class ScheddQuery:
    
    def __init__(self, schedd_name):
        self.scheddName=schedd_name
    
    def execute(self, completed_since, jobs, history):
        # Get timestamp for upcoming condor_history call (this will be the next
        # completedSince), add results from condor_history if appropriate.
        q_data = self.getCurrent(jobs)
        
        if history:
            # Case 5244: job history can get missed
            # New to v1.8: get the time before the command (which we did prior to v1.5), and 
            # also subtract an offset to make some overlap
            completedSince = long(time.time()) - COMPLETED_SINCE_OVERLAP
            return_time  = "-- CompletedSince: " + str(completedSince) + "\n"
            history_data = self.getHistory(completed_since, jobs)
            data         = q_data + return_time + history_data
        else:
            data = q_data
        return data
    
    def getCurrent(self, jobs):
        # Get results from condor_q
        q_cmd = 'condor_q -name %s -long %s' % (self.scheddName, jobs)
        logging.info("condor_q command: %s" %q_cmd)
        q_data, err_data = util.runCommand(q_cmd)
        if err_data != '':
            # We really should be checking the return code but that's not available
            raise Exception("Executing condor_q command:\n%s" %err_data)
        return q_data
    
        
    def getHistory(self, completed_since, jobs):
        history_file = util.getCondorConfigVal("HISTORY", "schedd", self.scheddName)
        if history_file == None:
            raise Exception("History is not enabled on this scheduler")
        # Case 5458: Consider an empty string value for HISTORY to be the same as None
        # and raise an exception.
        if len(history_file.strip()) == 0 :
            raise Exception("The HISTORY setting is an empty string")
        logging.info("History file for daemon %s: %s"%(self.scheddName, history_file))
        files        = glob.glob(history_file + "*")
        history_data = ''
        for f in files:
            if os.path.isfile(f):
                mod = os.path.getmtime(f)
                # allow for some overlap when testing
                if mod >= (completed_since - COMPLETED_SINCE_OVERLAP):
                    # each output from condor_history has a trailing newline so we can
                    # just concatenate them
                    history_data = history_data + self.getHistoryFromFile(completed_since, jobs, f)
                else:
                    logging.info("History file %s was last modified before given completedSince, skipped" % os.path.basename(f))
        return history_data
    
    
    def getHistoryFromFile(self, completed_since, jobs, history_file):
        history_data = ''
        err_data     = ''
        if jobs != "":
            history_cmd = 'condor_history -l -f %s %s' % (history_file, jobs)
        else:
            # note: we use EnteredCurrentStatus because some jobs may have been removed,
            # so they have no CompletionDate
            history_cmd = 'condor_history -l -f %s -constraint "EnteredCurrentStatus >= %s"' % (history_file, completed_since)
        history_data, err_data = util.runCommand(history_cmd)
        if err_data != '':
            raise Exception("Executing condor_history command:\n%s" %err_data)
        return history_data
    

