#!/usr/bin/env python

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
import os
import re
import sys
import zipfile
import tempfile
import glob
import util
import pickle
import logging
import urllib
import string
import shutil
import CondorAgent.util

################################################################################
# GLOBALS
################################################################################


################################################################################
# CLASSES
################################################################################


################################################################################
# METHODS
################################################################################
def do_submit(handler, submitDir=None):
    
    logging.debug('CondorAgent.post_submit.do_submit(): handler.path: %s' % (handler.path))
    # Parse out the queue=(.*) stuff from the path. Assume it's pretty simple and regular
    # in form for now. We have to do this because urlparse.parse_qs() isn't in urlparse
    # in Python 2.4.x. Argh.
    parse_qs_re = re.compile('queue=(\S+)')
    match_obj = parse_qs_re.search(handler.path)
    queue_name = None
    if match_obj:
        queue_name = urllib.unquote(match_obj.group(1))
    
    type = handler.headers.get('Content-type')    
    if (type != 'application/zip'):
        raise Exception("Content type is not application/zip")
    
    # Copy and extract the zip file to a unique directory in our working dir.
    if not submitDir:
        raise Exception('Missing valid submission directory')
    if not os.path.isdir(submitDir):
        # Don't create it. We expect the caller to have done this if this is
        # what they wanted to happen.
        raise Exception('Submission directory %s does not exist' % submitDir)
        
    tmpDir = tempfile.mkdtemp(dir=submitDir)
    os.chmod(tmpDir, 0777)
    logging.debug('CondorAgent.post_submit.do_submit(): submitting from temporary submission directory \'%s\'' % tmpDir)
    
    # Get the zip file from the request.
    length = int(handler.headers.get('Content-length'))
    zipname = os.path.join(tmpDir, "submit.zip")    
    zipfp = open(zipname, 'wb')
    logging.debug('Writing compressed submission data to %s' % zipname)
    try:
        zipfp.write(handler.rfile.read(length))
    finally:
        zipfp.close()
    
    submitZip = zipfile.ZipFile(zipname, "r")
    
    # Now uncompress the zip file, creating any directories necessary.
    for name in submitZip.namelist():
        if name[-1] == '/':
            logging.debug('Making new submission sub-directory %s' % os.path.join(tmpDir, name))
            os.makedirs(os.path.join(tmpDir, name))
        else:
            data = submitZip.read(name)
            fp = open(os.path.join(tmpDir, name), 'w')
            logging.debug('Extracting file %s from submission archive %s' % (os.path.join(tmpDir, name), zipname))
            try:
                fp.write(data)
            finally:
                fp.close()
    
    # Now we have a directory with the submit files in it. 
    # Ensure there is one and only one .sub or .submit file.
    submitFiles = []
    logging.debug('Searching %s for .sub files' % tmpDir)
    for subFile in locate("*.sub", tmpDir):
        submitFiles.append(subFile)
    
    logging.debug('Searching %s for .submit files' % tmpDir)
    for subFile in locate("*.submit", tmpDir):
        submitFiles.append(subFile)
    
    logging.debug('Found %s .sub and .submit files in %s' % (len(submitFiles), tmpDir))
    
    if len(submitFiles) == 0:
        # no submit files found.
        try:
            for s in submitFiles:
                os.remove(s)
            os.remove(zipname)
        except Exception, e:
            logging.warn('Unable to remove submission file and zip file')
        raise Exception("Zero submit files found. Submit requests must contain a submit file.")
    if len(submitFiles) > 1:
        # too many submit files found
        try:
            for s in submitFiles:
                os.remove(s)
            os.remove(zipname)
        except Exception, e:
            logging.warn('Unable to remove submission file and zip file')
        raise Exception("%d submit files discovered. Submit requests must contain only one submit file." % len(submitFiles))
    clusterId = doCondorSubmit(submitFiles[0], queue_name)
    
    # Remove the submission files but leave the zip file in case we need to debug
    try:
        for s in submitFiles:
            os.remove(s)
    except Exception, e:
        # Not a critical error at this point since we'll clean up this dir
        # after the jobs have run to completion.
        logging.warn('Unable to remove submission file')
    
    # Make sure the remaining files have open permissions
    try:
        for f in locate("*.*", tmpDir):
            os.chmod(f, 0666)
    except Exception, e:
        logging.warn('Unable chmod Condor output files')
    
    # Write out a stub for this submission so the cleanup utility can find it
    # check to see if it still exists, and if not: delete the data file.
    pfile = os.path.join(submitDir, '%s-%s.cluster' % (str(queue_name), str(clusterId)))
    logging.debug('Dumping pickled cluster information to %s' % pfile)
    t = { 'clusterid' : clusterId, 'queue' : queue_name, 'tmpdir' : tmpDir }
    try:
        fp = open(pfile, 'wb')
    except Exception, e:
        logging.error('Unable to dump cluster information to %s: %s' % (pfile, str(e)))
    else:
        pickle.dump(t, fp)
        fp.close()
        logging.debug('Cluster details dumped successfully')
    
    return clusterId


def locate(pattern, root=os.curdir):
    '''Locate all files matching supplied filename pattern in and below
    supplied root directory.'''
    for path, dirs, files in os.walk(os.path.abspath(root)):
        for matches in glob.glob(os.path.join(path, pattern)):
            yield os.path.join(path, matches)


def doCondorSubmit(submitFile, queueName):
    clusterId = None
    # Change to the Condor directory
    (submission_directory, filename) = os.path.split(submitFile)
    
    logging.debug("CondorAgent.post_submit.doCondorSubmit(): submission file: %s" % submitFile)
    # TODO: make sure condor_submit is in the path and is available.
    submit_cmd = ['condor_submit']
    if queueName:
        submit_cmd.append('-name')
        submit_cmd.append('%s' % queueName)
    # Case 7108: Add a new configuration option that allows users to pass along custom command
    # line arguments to insert in to the condor_submit call made by Condor Agent.
    # The syntax for the option is a comma-seperated list. With each value in the list being
    # an element in the command line argument
    additional_arguments = []
    add_str = CondorAgent.util.getCondorConfigVal('CONDOR_AGENT_SUBMIT_PROXY_ADDITIONAL_ARGUMENTS')
    if add_str:
        # Add cleaned up versions of the arguments to our array
        additional_arguments = [i.strip() for i in string.split(add_str, ',')]
    if len(additional_arguments) > 0:
        logging.debug("CondorAgent.post_submit.doCondorSubmit(): Adding additional, user supplied arguments: %s" % ' '.join(additional_arguments))
        submit_cmd.extend(additional_arguments)
    submit_cmd.append('%s' % submitFile)
    logging.debug("CondorAgent.post_submit.doCondorSubmit(): condor_submit command: %s" % ' '.join(submit_cmd))
    
    # Set the umask to be liberal so files that get created can be edited by anyone
    current_umask = os.umask(0)
    
    try:
        (retcode, submit_out, submit_err) = util.runCommand2(cmd=' '.join(submit_cmd), cwd=submission_directory)
    except:
        # TODO: determine exact error condition check. Possible that there are still warnings we should log.
        logging.error("CondorAgent.post_submit.doCondorSubmit(): Unexpected error: %s, %s" % (sys.exc_info()[0], str(sys.exc_info()[1])))
        if submit_out:
            logging.error("CondorAgent.post_submit.doCondorSubmit(): submit_out: %s" % submit_out)
        if submit_err:
            logging.error("CondorAgent.post_submit.doCondorSubmit(): submit_err: %s" % submit_err)
        os.umask(current_umask)
        cleanSubmissionDir(submission_directory)
        raise
    os.umask(current_umask)
    logging.debug("CondorAgent.post_submit.doCondorSubmit(): retcode:    %d" % retcode)
    logging.debug("CondorAgent.post_submit.doCondorSubmit(): submit_out: %s" % submit_out)
    logging.debug("CondorAgent.post_submit.doCondorSubmit(): submit_err: %s" % submit_err)
    if retcode == 0:
        match = re.search("submitted to cluster (\\d+)", submit_out)
        if match == None:
            logging.error('CondorAgent.post_submit.doCondorSubmit(): Unable to parse submission details from condor_q output')
            cleanSubmissionDir(submission_directory)
            raise Exception("Failed to parse cluster id from output:\n%s" % submit_out)
        clusterId = match.group(1)
    else:
        # TODO: parse the error to figure out what happened.
        logging.error('CondorAgent.post_submit.doCondorSubmit(): Condor submission failed')
        cleanSubmissionDir(submission_directory)
        raise Exception("Failed to submit jobs to condor with error:\n%s" % submit_err)
    logging.info('CondorAgent.post_submit.doCondorSubmit(): Returning cluster ID: %s' % str(clusterId))
    return clusterId

def cleanSubmissionDir(submission_dir):
    '''
    Cleans up after a failed submission attempt so disk pollution does not get
    so bad. Does some minimal checking on submission_dir to make sure it
    does not do something stupid like delete / or something like that.
    '''
    submit_dir_expected_prefix = CondorAgent.util.getCondorConfigVal("CONDOR_AGENT_SUBMIT_DIR").replace('"', '')
    if submit_dir_expected_prefix and submit_dir_expected_prefix != '' and len(submit_dir_expected_prefix) > 3:
        # That's a bit of a lame check, >3 -- it could be better. On !Windows it should suffice to
        # stop someone from accidentally removing /. On Windows it should suffice to stop someone
        # from accidentally removing something like C:\. In truth this path should always be longer
        # than 3 characters...so for now we'll say it's good enough.
        # TODO Improve the safety checks before we try to delete things off of disk
        if submission_dir.find(submit_dir_expected_prefix) > -1:
            # We will trust that the user wasn't stupid enough to use / as their
            # submission dir.
            if os.path.isdir(submission_dir):
                logging.info('Cleaning up directory %s after failed submission' % submission_dir)
                try:
                    shutil.rmtree(submission_dir)
                except Exception, e:
                    logging.error('Unable to clean up %s after failed submission: %s' % (submission_dir, str(e)))
            else:
                logging.warning('Skipped cleanup of %s after failed submission, path is not a directory' % submission_dir)
        else:
            logging.warning('Skipped cleanup of %s after failed submission, path does not start with expected string "%s"' % (submission_dir, submit_dir_expected_prefix))
    else:
        logging.warning('Skipped cleanup of %s after failed submission, we do not trust submit directory prefix "%s"' % submit_dir_expected_prefix)

        

if __name__ == '__main__':
    pass