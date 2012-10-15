#!/usr/bin/python
# vim: set fileencoding=utf-8 ts=2 sw=2 expandtab :

import os,sys
from optparse import OptionParser,make_option
from time import sleep
from syslog import *
from pyinotify import *

__author__ = "Benedikt Böhm"
__copyright__ = "Copyright (c) 2007-2008 Benedikt Böhm <bb@xnull.de>"
__version__ = 0,2,3

OPTION_LIST = [
  make_option(
      "-c", dest = "config",
      default = "/etc/inosync/default.py",
      metavar = "FILE",
      help = "load configuration from FILE"),
  make_option(
      "-d", dest = "daemonize",
      action = "store_true",
      default = False,
      help = "daemonize %prog"),
  make_option(
      "-p", dest = "pretend",
      action = "store_true",
      default = False,
      help = "do not actually call rsync"),
  make_option(
      "-v", dest = "verbose",
      action = "store_true",
      default = False,
    ),
  make_option(
      "-s", dest = "singlefile",
      action = "store_true",
      default = False,
      help = "rsync single file"),
]

DEFAULT_EVENTS = [
    "IN_CLOSE_WRITE",
    "IN_CREATE",
    "IN_DELETE",
    "IN_MOVED_FROM",
    "IN_MOVED_TO"
]

class RsyncEvent(ProcessEvent):
  pretend = None

  def __init__(self, pretend=False, singlefile=False):
    self.pretend = pretend
    self.singlefile = singlefile
    syslog('Starting RsyncEvent. singlefile:%s' % self.singlefile)

  def sync(self):
    args = [config.rsync, "-ltrp", "--delete"]
    if config.extra:
      args.append(config.extra)
    args.append("--bwlimit=%s" % config.rspeed)
    if config.logfile:
      args.append("--log-file=%s" % config.logfile)
    if "rexcludes" in dir(config):
      for rexclude in config.rexcludes:
        args.append("--exclude=%s" % rexclude)
    args.append(config.wpath)
    args.append("%s")
    cmd = " ".join(args)
    for node in config.rnodes:
      if self.pretend:
        syslog("would execute `%s'" % (cmd % node))
      else:
        if self.singlefile:
          syslog('singlefile %s' % cmd)
        else:
          syslog(LOG_DEBUG, "executing %s" % (cmd % node))
          proc = os.popen(cmd % node)
          for line in proc:
            syslog(LOG_DEBUG, "[rsync] %s" % line.strip())

  def sync_single_file(self, file):
    syslog('sync_single_file:%s' % file)
    args = [config.rsync, "-ltrp"]
    if config.logfile:
      args.append("--log-file=%s" % config.logfile)
#    args.append(config.wpath)
    args.append(file)
    args.append('%s'+file)
    cmd = " ".join(args)
    for node in config.rnodes:
      if self.pretend:
        syslog('would execute %s' % (cmd % node))
      else:
          syslog(LOG_DEBUG, "executing %s" % (cmd % node))
          proc = os.popen(cmd % node)
          for line in proc:
            syslog(LOG_DEBUG, "[rsync] %s" % line.strip())

  def sync_parent_dir(self, path):
    '''This method takes the path to a file and does an rsync on the parent dir. The purpose
          for this is simply to apply deletions'''
    syslog(LOG_DEBUG, 'sync_parent_dir for path %s' % path)
    _sync_path = path[0:path.rfind('/')+1]
    cmd = 'rsync -avz --delete %s %%s%s' % (_sync_path, _sync_path )

    for node in config.rnodes:
      if self.pretend:
        syslog(LOG_DEBUG , 'would execute %s ' % (cmd % node))
      else:
        syslog(LOG_DEBUG, "executing %s" % (cmd % node))
        proc = os.popen(cmd % node)
        for line in proc:
          syslog(LOG_DEBUG, "[rsync] %s" % line.strip())





  def process_default(self, event):
    syslog(LOG_DEBUG, "caught %s on %s" % \
        (event.maskname, os.path.join(event.path, event.name)))
    if self.singlefile:
      if event.maskname == str('IN_CLOSE_WRITE'):
        self.sync_single_file(os.path.join(event.path, event.name))
      elif event.maskname == str('IN_DELETE'):
        self.sync_parent_dir(os.path.join(event.path, event.name))
      else:
        syslog('Ignored event %s' % event.maskname)
    else:
      self.sync()

def daemonize():
  try:
    pid = os.fork()
  except OSError, e:
    raise Exception, "%s [%d]" % (e.strerror, e.errno)

  if (pid == 0):
    os.setsid()
    try:
      pid = os.fork()
    except OSError, e:
      raise Exception, "%s [%d]" % (e.strerror, e.errno)
    if (pid == 0):
      os.chdir('/')
      os.umask(0)
    else:
      os._exit(0)
  else:
    os._exit(0)

  os.open("/dev/null", os.O_RDWR)
  os.dup2(0, 1)
  os.dup2(0, 2)

  return 0

def load_config(filename):
  if not os.path.isfile(filename):
    raise RuntimeError, "configuration file does not exist: %s" % filename

  configdir  = os.path.dirname(filename)
  configfile = os.path.basename(filename)

  if configfile.endswith(".py"):
    configfile = configfile[0:-3]

  sys.path.append(configdir)
  exec("import %s as __config__" % configfile)
  sys.path.remove(configdir)

  global config
  config = __config__

  if not "wpath" in dir(config):
    raise RuntimeError, "no watch path given"
  if not os.path.isdir(config.wpath):
    raise RuntimeError, "watch path does not exist: %s" % config.wpath
  if not os.path.isabs(config.wpath):
    config.wpath = os.path.abspath(config.wpath)

  if not "rnodes" in dir(config) or len(config.rnodes) < 1:
    raise RuntimeError, "no remote nodes given"

  if not "rspeed" in dir(config) or config.rspeed < 0:
    config.rspeed = 0

  if not "emask" in dir(config):
    config.emask = DEFAULT_EVENTS
  for event in config.emask:
    if not event in EventsCodes.ALL_FLAGS.keys():
      raise RuntimeError, "invalid inotify event: %s" % event

  if not "edelay" in dir(config):
    config.edelay = 10
  if config.edelay < 0:
    raise RuntimeError, "event delay needs to be greater or equal to 0"

  if not "logfile" in dir(config):
    config.logfile = None

  if not "extra" in dir(config):
    config.extra = ""
  if not "rsync" in dir(config):
    config.rsync = "/usr/bin/rsync"
  if not os.path.isabs(config.rsync):
    raise RuntimeError, "rsync path needs to be absolute"
  if not os.path.isfile(config.rsync):
    raise RuntimeError, "rsync binary does not exist: %s" % config.rsync

def main():
  version = ".".join(map(str, __version__))
  parser = OptionParser(option_list=OPTION_LIST,version="%prog " + version)
  (options, args) = parser.parse_args()

  if len(args) > 0:
    parser.error("too many arguments")

  logopt = LOG_PID|LOG_CONS
  if not options.daemonize:
    logopt |= LOG_PERROR
  openlog("inosync", logopt, LOG_DAEMON)
  if options.verbose:
    setlogmask(LOG_UPTO(LOG_DEBUG))
  else:
    setlogmask(LOG_UPTO(LOG_INFO))

  load_config(options.config)

  if options.daemonize:
    daemonize()

  wm = WatchManager()
  ev = RsyncEvent(options.pretend, options.singlefile)
  notifier = AsyncNotifier(wm, ev, read_freq=config.edelay)
  mask = reduce(lambda x,y: x|y, [EventsCodes.ALL_FLAGS[e] for e in config.emask])
  wds = wm.add_watch(config.wpath, mask, rec=True, auto_add=True)

  if not options.singlefile:
    syslog(LOG_DEBUG, "starting initial synchronization on %s" % config.wpath)
    ev.sync()
    syslog(LOG_DEBUG, "initial synchronization on %s done" % config.wpath)

  syslog("resuming normal operations on %s" % config.wpath)
  asyncore.loop()
  sys.exit(0)

if __name__ == "__main__":
  main()
