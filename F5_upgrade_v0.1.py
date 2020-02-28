#!/home/user/python36/bin/python3

"""
This program is  intended to upgrade the F5BigIP.

"""

import sys
import os
import threading
import getpass
import re
from datetime import *
import logging
import requests
import json
import time
from f5.bigip import ManagementRoot
from f5.utils.responses.handlers import Stats

big_ip = input('Enter the IP address of the BigIP: ')
user_name = input('Enter Username: ')
user_pass = getpass.getpass('Enter the password: ')
bigip_iso = input('Specify the iso file name: ')
CRQ = input('Enter the change #: ')
ucs_name = 'pre_' + CRQ + '.ucs'
base = 'https://' + big_ip

# Connect to the BigIP
try:
    mgmt = ManagementRoot(big_ip, user_name, user_pass)
except requests.HTTPError as err:
    sys.exit('Connection not established')
except KeyboardInterrupt:
    print('Manually interrupted')
except Exception:
    sys.exit('Connection not established, error ')
else:
    print('Connection established ...')

ltm = mgmt.tm.ltm
virtuals = mgmt.tm.ltm.virtuals
vip = mgmt.tm.ltm.virtuals.virtual
version = mgmt.tmos_version
devname = mgmt.tm.sys.global_settings.load()
hostname = devname.hostname
file_transfer = mgmt.shared.file_transfer
autodeploy = mgmt.cm.autodeploy
bash = mgmt.tm.util.bash.exec_cmd

# Basic Parameters
""" This is where the details of logging get set """
logger = logging.getLogger(__name__)
path = os.getcwd() + os.sep + "F5_upgrade.txt"
formatter = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
)
#
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
console_handler.setLevel(logging.INFO)
logger.addHandler(console_handler)
#
file_handler = logging.FileHandler(path)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
file_handler.setLevel(logging.INFO)
logger.info("Logging into file '{0}'".format(path))


class Prechecks:
    """ performs the pre checks prior upgrading """

    def __init__(self, ltm, virtuals, version):
        self.ltm = ltm
        self.virtuals = virtuals
        self.version = version

    def check_version(self):
        """ Checks the version compatibility """
        vn = mgmt.tmos_version.split('.')
        f5_version = int(vn[0])
        if f5_version < 11:
            sys.exit('The current version is ' + version + '. Do not proceed')
        elif f5_version < 14:
            print('The current version is ' + version + '. The upgrade can proceed')
        else:
            logger.info('Upgrade not needed')
            sys.exit('The device does not need upgrading')

    def check_ha(self):
        """ Checks to ensure upgrade will be performed on the standby device first """
        ha = mgmt.tm.cm.devices.device.load(name=hostname)
        if ha.failoverState == 'standby':
            print(hostname + ha.chassisId + ' is a standby device. The checks will continue')
        else:
            logger.info('Please start with Standby first')
            sys.exit(hostname + ha.chassisId + ' is not a standby device, Please start with Standby first')

    def sync_state(self):
        """ Checks the sync status of the bigIP and proceeds if changes in sync """
        s_state = bash('run', utilCmdArgs='-c "tmsh show /cm sync-status"')
        print(s_state.commandResult)
        s_state = s_state.commandResult
        c_state = s_state.split('\n')
        if 'green' in (c_state[4]):
            print('This device is in sync. Implementation will continue')
        else:
            logger.info('Need to sync changes first')
            sys.exit('Program was stopped because the changes are not in sync')

    def license_checkdate(self):
        """ static mapping of versions to license dates """
        tar_ver = input('What is the target firmware version?, eg 13.1.3: ')
        ver_mapping = {'15.1.0': '2019-11-05', '15.0.0': '2019-05-03', '15.0.1': '2019-05-03', '14.1.0': '2018-10-25',
                       '14.9.0': '2018-10-25', '14.0.0': '2018-07-11', '14.0.9': '2018-07-11', '13.1.0': '2017-09-12',
                       '13.1.3': '2017-09-12', '13.0.0': '2017-01-13', '13.0.1': '2017-01-13', '12.1.0': '2016-03-18',
                       '12.1.5': '2016-03-18'}
        tar_ver = ver_mapping.get(tar_ver)
        y1, m1, d1 = tar_ver.split('-')
        y1, m1, d1 = int(y1), int(m1), int(d1)
        lic_chk_date = date(y1, m1, d1)

        """
        If the service check date is missing or is earlier than the license check date, the system will fail to load 
        the configuration when you attempt to boot into the upgraded software slot.
        """
        serv_chk_output = mgmt.tm.util.bash.exec_cmd('run', utilCmdArgs='-c "tmsh show sys license | grep Service"')
        serv_chk_date = re.findall('\d+.\d+.\d+', serv_chk_output.commandResult)
        serv_chk_date = serv_chk_date[0]
        y2, m2, d2 = serv_chk_date.split('/')
        y2, m2, d2 = int(y2), int(m2), int(d2)
        serv_chk_date = date(y2, m2, d2)

        if lic_chk_date >= serv_chk_date:
            logger.info("License Check Date: {0} Service Check Date: {1}".format(lic_chk_date, serv_chk_date))
            sys.exit('Halt the upgrade! The service check date is earlier than the license check date.')
        else:
            logger.info("License Check Date: {0} Service Check Date: {1}".format(lic_chk_date, serv_chk_date))
            logger.info('You are good to proceed! The service check date is later than the license check date.')

    def current_vips(self):
        """ Outputs all vips configured in all partitions """
        existing_vips = virtuals.get_collection()
        for v in existing_vips:
            print('Currently configured vip:  ', v.name)

    def pool_members(self):
        """ Outputs all pools configured in all partitions """
        existing_pools = mgmt.tm.ltm.pools.get_collection()
        for pool in existing_pools:
            print('Currently configured pool:  ', pool.name)
        print('\n ' * 5)

    def available_vips(self):
        """ Shows VIP status as a pre-check """
        existing_vips = virtuals.get_collection()
        vip_list = {}
        for item in existing_vips:
            vip_list[item.destination] = item.name

        print('I have found {0} VIPs '.format(len(vip_list)))
        print('*' * 100)

        for k, v in vip_list.items():
            partition_split = k.split('/')
            partition = partition_split[1]
            vip = ltm.virtuals.virtual.load(name=v, partition=partition)
            vip_stats = Stats(vip.stats.load())
            virt_server = vip_stats.stat['destination']['description']
            status = vip_stats.stat['status_availabilityState']['description']
            logger.info('Partition :{0:<15} VIP Name: {1:<45} VIP IP:{2:<35}Status: {3:<15}'.format(partition, v,
                                                                                                    virt_server,
                                                                                                    status))


class Backup:
    """ Class to create and download backup archives """
    def __init__(self, file_transfer):
        self.file_transfer = file_transfer

    def create_ucs(self):
        """ Creates a Backup archive pre upgrade """
        print('Creating Backup please wait...')
        save_config = bash('run', utilCmdArgs='-c "tmsh save sys config"')
        print(save_config.commandResult)
        createucs = bash('run', utilCmdArgs='-c "tmsh save sys ucs /var/local/ucs/pre_"' + CRQ)
        print(createucs.commandResult)
        logger.info('ucs archive was created successfully')

    def report_progress(self, path):
        """ Generates log message to track progress """
        size_kb = os.stat(path).st_size / 1024
        print(f"Download in progress ({size_kb:.2f}KB downloaded)...")

    def download_with_progress(download_ucs, *args, update_frequency=1):
        """ Periodically provides feed back to indicate to user that download is progressing"""
        progress = threading.Event()
        print(f"Executing {Backup.download_ucs.__name__}")
        thr = threading.Thread(target=Backup.download_ucs, args=(*args,))
        print("Download started")
        thr.start()
        while thr.isAlive():
            progress.wait(update_frequency)
            Backup.report_progress(*args)
        print("Download completed")
        logger.info('ucs archive downloaded')

    def download_ucs(src, dst):
        """ Downloads the backup ucs file to local directory """
        backup_ucs = file_transfer.ucs_downloads.download_file(ucs_name, ucs_name)
        print('Downloading the ucs file may take several minutes. Please be patient')


class Execution:
    def __init__(self, autodeploy):
        self.autodeploy = autodeploy

    def imageupload(self, *args):
        print('This operation will take around 30 minutes')
        image_transfer = autodeploy.software_image_uploads.upload_image(bigip_iso)
        print("Image has been successfully uploaded to '/shared/images/' folder")

    def upload_with_progress(imageupload, *args, update_frequency=1):
        """ Periodically provides feed back to indicate to user that download is progressing"""
        prog = threading.Event()
        print(f"Executing {Execution.imageupload.__name__}")
        thr = threading.Thread(target=Execution.imageupload, args=(*args,))
        print("Upload started")
        thr.start()
        while thr.isAlive():
            prog.wait(update_frequency)
            print('Uploading in progress, Please wait... ')
        logger.info("Image upload completed")

    def checkactivehd(self):
        """ checks which volume is available for iso install """
        activehd = bash('run', utilCmdArgs='-c "tmsh show /sys software status"')
        print(activehd.commandResult)
        activevol = activehd.commandResult
        activevol = activevol.split('\n')
        inactive = []
        for i in activevol:
            if 'yes' in i:
                global use_active
                use_active = i
                print('\n' * 5, 'Active volume, The volume is not available to install ----> ', i, '\n' * 5, '-' * 400)
            elif 'HD' in i and 'yes' not in i:
                inactive.append(i)
        inactiv = str(inactive)
        sequ = re.findall('HD\d{1,}.\d{1,}', inactiv)
        for i, j in enumerate(sequ):
            print(i,'    ',  j, '        Available to install')

    def installimage(self):
        """ verifies and installs the iso in requested volume """
        global choosevol
        choosevol = input('Choose an available volume to install.e.g HD1.2: ').upper()
        while True:
            if choosevol in use_active:
                print('This volume is already active. Choose another option')
                choosevol = input('Enter the volume to activate: ')
            else:
                break
        req = requests.session()
        req.trust_env = False
        url = base + '/mgmt/tm/sys/software/image'
        headers = {
            'Content-Type': "application/json",
            'cache-control': "no-cache"
        }
        payload = {"command": "install", "name": bigip_iso, "volume": choosevol}
        response = req.request('POST', url, headers=headers, data=json.dumps(payload), auth=(user_name, user_pass),
                               verify=False)
        post_resp = str(response.status_code)
        if '200' in post_resp:
            print('The image is being installed in', choosevol, 'Please be patient')
            time.sleep(5)
        else:
            try:
                resp_error = response.json()
                print(f'''Error {resp_error['code']}: {resp_error['message']}''')
            except Exception:
                logger.info('The program was unable to install the image in selected volume. Error: ', post_resp)
            return False
        status_list = 'testing'
        while 'testing' in status_list or 'installing' in status_list:
            print('Installation in progress...')
            time.sleep(5)
            cstaterun = bash('run', utilCmdArgs='-c "tmsh show /sys software status"')
            cstate = cstaterun.commandResult
            cstate = cstate.split('\n')
            status_list = []
            for status in cstate:
                if choosevol in status:
                    status_list.append(status)
                    print(status_list)
                    status_list = status_list[0]
        if 'complete' in status_list:
            logger.info('Installation Completed Successfully')
            return True
        else:
            logger.info('Installation was not successful')
            return False

    def copy_config(self):
        """migrates configuration to the newly installed volume"""
        global bootable
        cstaterun = bash('run', utilCmdArgs='-c "tmsh show /sys software status"')
        print(cstaterun.commandResult)
        bootable = input('Enter the volume to install active configuration: ').upper()
        while True:
            if bootable in use_active:
                print('This volume is already active. Choose another option')
                bootable = input('Enter the volume to activate: ')
            else:
                break
        acti = use_active.split(' ')
        acti = acti[0]
        cpcmd = f"-c 'cpcfg --source={acti} {bootable}'"
        try:
            exec_cpcmd = bash('run', utilCmdArgs=cpcmd)
            print('configuration is being copied from', acti, exec_cpcmd.commandResult)
            logger.info('Configuration copied')
            return True
        except Exception:
            logger.info('Critical Error, Configuration was not copied to new volume')
            return False

    def reboot_vol(self):
        """reboots and activates the selected volume"""
        boot = f"-c 'reboot volume {bootable}'"
        try:
            exec_boot = bash('run', utilCmdArgs=boot)
            print(exec_boot.commandResult)
            logger.info('Device rebooting... Monitor console output')
            return True
        except Exception:
            logger.info('Program was unable to initiate system reboot')
            return False


class Postchecks:
    """ Performs the validation checks post the upgrade """
    def __init__(self, version):
        self.version = version
        new_v = mgmt.tmos_version
        print('The current version of the BigIP is: ', new_v)


if __name__ == '__main__':

    frame = input("This program is  intended to upgrade the F5BigIP. Press any key to begin or type 'quit' to abort: ")
    if frame == 'quit':
        sys.exit('=====Aborted=====')
    elif frame != 'quit':
        pass
    # Hash out the functions not intended to be used
    try:
        if sys.argv[1] == 'pre':
            pre_checks = Prechecks(ltm, virtuals, version)
            pre_checks.check_version()
            pre_checks.check_ha()
            pre_checks.sync_state()
            pre_checks.license_checkdate()
            pre_checks.current_vips()
            pre_checks.pool_members()
            pre_checks.available_vips()
            logger.info('PreChecks have been completed.')
        elif sys.argv[1] == 'execute':
            bck = Backup(file_transfer)
            bck.create_ucs()
            bck.download_with_progress(Backup.download_ucs, ucs_name, update_frequency=5)
            execute = Execution(autodeploy)
            execute.upload_with_progress(Execution.imageupload, bigip_iso, update_frequency=5)
            execute.checkactivehd()
            success = execute.installimage()
            if not success:
                sys.exit('Image installation failed. Contact Administrator')
            success = execute.copy_config()
            if not success:
                sys.exit('Critical Error, Configuration was not copied to new volume')
            success = execute.reboot_vol()
            if not success:
                sys.exit('Unable to reboot system to finish the upgrade')
            logger.info('Execution Steps completed, Device rebooting, This process may take a few minutes')
        elif sys.argv[1] == 'validate':
            print('Running PostChecks...', '\n' * 5)
            post_check = Postchecks(version)
            pre_checks = Prechecks(ltm, virtuals, version)
            pre_checks.current_vips()
            pre_checks.pool_members()
            pre_checks.available_vips()
            logger.info('The Upgrade was validated')
            print('End of program...', '\n' * 5)
    except IndexError:
        sys.exit('Implementation stage not specified. eg: pre, execute or validate.')
