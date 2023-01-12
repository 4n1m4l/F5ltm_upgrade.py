#!/usr/bin/env python
import requests

user_input_ip = input('Enter the Host IP address to scan: ')
scandb = 'https://internetdb.shodan.io/' + user_input_ip
host = requests.get(scandb).json()

if 'detail' in host:
    print ('No information Available')
else:
    print (host)
    print ('Hostnames: ', (host['hostnames']))
    print ('IP: ', (host['ip']))
    print ('Open Ports: ', (host['ports']))
    print ('vulnerabilites: ', (host['vulns']))
