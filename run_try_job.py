#!/usr/bin/env python

import argparse
import sys

from util.base_util import *
from util.project_util import *
from os import path

TRY_JOB_CONFIG = path.join(path.dirname(path.abspath(__file__)), 'try_job.json')

def parse_arguments():
  parser = argparse.ArgumentParser(
      description='Run try jobs\n'\
                  'The test configuration is defined in try_job.json.\n\n',
      formatter_class=argparse.RawTextHelpFormatter)
  parser.add_argument('--type', '-t',
      choices=['release', 'debug', 'default'], default='release',
      help='Browser type. Default is \'release\'.\n'\
           'release/debug/default assume that the binaries are\n'\
           'generated into out/Release or out/Debug or out/Default.\n\n')
  parser.add_argument('--chrome-dir', '-c',
      help='Chrome source directory.\n\n')
  parser.add_argument('--aquarium-dir', '-a',
      help='Aquarium source directory.\n\n')
  parser.add_argument('--build', '-b', action='store_true',
      help='Rebuild all targets before running tests.\n\n')
  parser.add_argument('--update', '-u', action='store_true',
      help='Fetch from origin and rebase current branch,\n'\
           'then synchronize the dependencies before building.\n'\
           '--build will be enabled automatically\n\n')
  parser.add_argument('--sync', '-s', action='store_true',
      help='Synchronize the dependencies before building.\n'\
           '--build will be enabled automatically\n\n')
  parser.add_argument('--email', '-e', action='store_true',
      help='Send the report by email.\n\n')
  args = parser.parse_args()

  if args.chrome_dir:
    args.chrome_dir = path.abspath(args.chrome_dir)
    if path.exists(path.join(args.chrome_dir, 'src')):
      args.chrome_dir = path.join(args.chrome_dir, 'src')

  if args.aquarium_dir:
    args.aquarium_dir = path.abspath(args.aquarium_dir)

  # Load configuration
  config = read_json(TRY_JOB_CONFIG)
  args.report_receivers = config['report_receivers']
  args.aquarium_average_fps = config['aquarium_average_fps']

  if is_win():
    args.try_jobs = config['win_jobs']
  elif is_linux():
    args.try_jobs = config['linux_jobs']
  elif is_mac():
    args.try_jobs = config['mac_jobs']

  args.try_job_target = config['try_job_target']
  args.try_job_shards = config['try_job_shards']

  return args


def update_aquarium_report(args, report):
  max_bias = 0
  lines = report.splitlines()
  for i in range(0, len(lines)):
    match = re_match(r'^aquarium_(.+)_test\s+(\d+)$', lines[i])
    if match:
      key, value = match.group(1), int(match.group(2))
      reference_value = args.aquarium_average_fps[get_osname()][key]
      bias = int(float(value - reference_value) * 100 / reference_value)
      lines[i] += ' (%s%d%%)' % ('+' if bias >= 0 else '', bias)
      if abs(bias) > abs(max_bias):
        max_bias = bias

  if max_bias:
    notice = ' [Max Bias:%s%d%%]' % ('+' if max_bias >= 0 else '', max_bias)
  else:
    notice = ' No Bias'
  title = 'Aquarium Test Report - %s / %s -%s' % (get_osname().title(), get_hostname(), notice)

  header = 'Location: %s\n' % os.getcwd()
  if args.aquarium_revision:
    header += 'Revision: %s\n' % args.aquarium_revision
  return title, header + '\n'.join(lines)


def update_test_report(args, target, report):
  flaky_pass = 0
  new_pass = 0
  new_fail = 0
  for line in report.splitlines():
    match = re_match(r'^.*\[Flaky Pass:(\d+)\].*$', line)
    if match:
      flaky_pass += int(match.group(1))
    match = re_match(r'^.*\[New Pass:(\d+)\].*$', line)
    if match:
      new_pass += int(match.group(1))
    match = re_match(r'^.*\[New Fail:(\d+)\].*$', line)
    if match:
      new_fail += int(match.group(1))

  notice = ''
  if new_fail:
    notice += ' [New Fail:%d]' % new_fail
  if new_pass and target == 'webgl':
    notice += ' [New Pass:%d]' % new_pass
  if flaky_pass and target == 'webgl':
    notice += ' [Flaky Pass:%d]' % flaky_pass
  if not notice:
    notice = ' All Clear'

  title = ''
  if target == 'webgl':
    title = 'WebGL Test'
  elif target == 'gtest':
    title = 'GTest'
  title += ' Report - %s / %s -%s' % (get_osname().title(), get_hostname(), notice)

  header = 'Location: %s\n' % os.getcwd()
  if args.chrome_revision:
    header += 'Revision: %s\n' % args.chrome_revision
  gpu, driver_version = get_gpu_info()
  if gpu:
    header += 'GPU: %s\n' % gpu
  if driver_version:
    header += 'Driver: %s\n' % driver_version
  return title, header + report


def build_project(project, args):
  build_cmd = ['build_project', project, '--type', args.type]
  if project == 'chrome':
    build_cmd.extend(['--dir', args.chrome_dir])
  elif project == 'aquarium':
    build_cmd.extend(['--dir', args.aquarium_dir])

  try:
    cmd = build_cmd[:]
    if args.update:
      cmd.append('--update')
    elif args.sync:
      cmd.append('--sync')
    execute_command_stdout(cmd)
  except CalledProcessError:
    execute_command(build_cmd, return_log=True)


def notify_command_error(receivers, error):
  send_email(receivers,
             '%s %s failed on %s' % (error.cmd[0], error.cmd[1], get_hostname()),
             '%s\n\n%s' % (' '.join(error.cmd), error.output))


def main():
  args = parse_arguments()
  aquarium_build_failed = False

  if args.chrome_dir:
    if args.build or args.update or args.sync:
      try:
        build_project('chrome', args)
      except CalledProcessError as e:
        notify_command_error(args.report_receivers['admin'], e)
        raise e
    args.chrome_revision = get_chrome_revision(args.chrome_dir)

  if args.aquarium_dir:
    if args.build or args.update or args.sync:
      try:
        build_project('aquarium', args)
      except CalledProcessError as e:
        notify_command_error(args.report_receivers['aquarium'], e)
        aquarium_build_failed = True
    args.aquarium_revision = get_aquarium_revision(args.aquarium_dir)

  # Run tests
  target_set = set()
  for job in args.try_jobs:
    target = args.try_job_target[job][0]
    backend = args.try_job_target[job][1]
    if target == 'aquarium' and aquarium_build_failed:
      continue

    cmd = ['run_gpu_test', target, '--backend', backend, '--type', args.type]
    if target == 'aquarium':
      assert args.aquarium_dir
      cmd.extend(['--dir', args.aquarium_dir])
    else:
      assert args.chrome_dir
      cmd.extend(['--dir', args.chrome_dir])

    for key in ['%s_%s' % (target, backend), target]:
      if args.try_job_shards.has_key(key):
        cmd.extend(['--shard', str(args.try_job_shards[key])])
        break

    try:
      execute_command(cmd, return_log=True)
      if target.startswith('webgl'):
        target_set.add('webgl')
      else:
        target_set.add(target)
    except CalledProcessError as e:
      notify_command_error(args.report_receivers['admin'], e)

  # Dump test results
  for target in target_set:
    try:
      report = execute_command(['parse_result', target], print_log=False, return_log=True)
      if report:
        if target == 'aquarium':
          title, report = update_aquarium_report(args, report)
        else:
          title, report = update_test_report(args, target, report)
        print('\n--------------------------------------------------\n%s\n\n%s' % (title, report))
        name = target
        if name != 'gtest':
          name += '_test'
        write_file(name + '_report.txt', report)
        if args.email:
          send_email(args.report_receivers[target], title, report)
    except CalledProcessError as e:
      notify_command_error(args.report_receivers['admin'], e)

  return 0


if __name__ == '__main__':
  sys.exit(main())
