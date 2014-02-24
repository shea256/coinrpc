#!/usr/bin/env python
# -*- coding: utf-8 -*-
#-----------------------
# Copyright 2014 Halfmoon Labs, Inc.
# All Rights Reserved
#-----------------------

from flask import Flask, request
from pymongo import Connection
from config import * 

app = Flask(__name__)

import json
import namecoinrpc
import getpass
from functools import wraps

namecoind = namecoinrpc.connect_to_remote(NAMECOIND_USER, NAMECOIND_PASSWD, 
                                        host=NAMECOIND_SERVER, port=NAMECOIND_PORT, 
                                        use_https=NAMECOIND_USE_HTTPS)

con = Connection()
db = con['namecoin']
queue = db.queue

entered_passphrase = ''

#---------------------------------
def check_auth(username, password):
    return username == APP_USERNAME and password == APP_PASSWORD

#---------------------------------
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth: 
            return error_reply("invalid username/password")

        elif not check_auth(auth.username, auth.password):
            return error_reply("invalid auth username/password")
        return f(*args, **kwargs)

    return decorated

#-------------------------
def pretty_dump(input):

    return json.dumps(input, sort_keys=False, indent=4, separators=(',', ': '), ensure_ascii=False)

#---------------------------------
def error_reply(msg, code = -1):
    reply = {}
    reply['status'] = code
    reply['message'] = "ERROR: " + msg
    return pretty_dump(reply)

#-----------------------------------
@app.route('/')
def index():
    return 'Welcome to the namecoind API server of <a href="http://halfmoonlabs.com">Halfmoon Labs</a>.'	

#-----------------------------------
@app.route('/namecoind/blocks')
def namecoind_blocks():
    reply = {}
    info = namecoind.getinfo()
    reply['blocks'] = info.blocks
    return pretty_dump(reply)

#-----------------------------------
#step-1 for registrering new names 
@app.route('/namecoind/name_new', methods = ['POST'])
@requires_auth
def namecoind_name_new():

    reply = {}
    data = request.values
    
    if not 'key' in data  or not 'value' in data:
        return error_reply("Required: key, value", 400)
        
    key = data['key']
    value = data['value']
    
    object_type = data.get('type')

    #----------------
    #if 'type' is not passed; we have default 'key'
    if object_type == "domain":
        key = 'd/' + key
    elif object_type == "onename":
        key = 'u/' + key

    #check if this key already exists
    status = json.loads(namecoind_is_key_registered(key))
    if status['status'] == True:
        return error_reply("This key already exists")
        
    #check if passphrase is valid
    if not unlock_wallet(entered_passphrase):
        return error_reply("Wallet passphrase is incorrect", 403)

    #create new name
    #returns a list of [longhex, rand]
    info = namecoind.name_new(key)
    
    reply['longhex'] = info[0]
    reply['rand'] = info[1]
    reply['key'] = key
    reply['value'] = value
    
    #get current block...
    info = namecoind.getinfo()
    reply['current_block'] = info.blocks
    reply['wait_till_block'] = info.blocks + 12
    reply['activated'] = False
    
    #save this data to Mongodb...
    queue.insert(reply)

    reply['message'] = 'Your registration will be completed in roughly two hours'
    del reply['_id']        #reply[_id] is causing a json encode error
    
    return pretty_dump(reply)

#----------------------------------------------
#step-2 for registering new names
def namecoind_firstupdate(name, rand, value):

    info = namecoind.name_firstupdate(name, rand, value)
    return json.dumps(info)


#-----------------------------------
@app.route('/namecoind/name_update', methods = ['POST'])
@requires_auth
def namecoind_name_update():

    reply = {}
    data = request.values

    if not 'key' in data or not 'new_value' in data:    
        return error_reply("Required: key, new_value", 400)
    
    key = data['key']
    new_value = data['new_value']
    
    #now unlock the wallet
    if not unlock_wallet(entered_passphrase):
        error_reply("Wallet passphrase is incorrect", 403)
        
    #update the 'value'
    info = namecoind.name_update(key, new_value)
    return pretty_dump(info)

#-----------------------------------
@app.route('/namecoind/transfer', methods = ['POST'])
@requires_auth
def namecoind_transfer():

    reply = {}
    data = request.values

    if not 'key' in data or not 'new_address' in data:    
        return error_reply("Required: key, new_address", 400)
    
    key = data['key']
    new_address = data['new_address']
    
    #check if this name exists and if it does, find the value field
    #Note that update command needs an arg of <new value>.
    #In case we're simply transferring, we need to obtain old value first

    key_details = json.loads(namecoind_get_key_details(key))

    if 'code' in key_details and key_details.get('code') == -4:
        return error_reply("Key does not exist")

    #get new 'value' if given, otherwise use the old 'value'
    value = data.get('value') if data.get('value') is not None else key_details.get('value')

    #now unlock the wallet
    if not unlock_wallet(entered_passphrase):
        error_reply("Wallet passphrase is incorrect", 403)
        
    #transfer the name
    info = namecoind.name_update(key, value, new_address)
    return pretty_dump(info)

#-----------------------------------
@app.route('/namecoind/check_registration')
def check_registration():

    reply = {}
    key = request.args.get('key')

    info = namecoind.name_show(key)
    
    if 'code' in info and info.get('code') == -4:
        reply['message'] = 'The key is not registered'
        reply['status'] = 404
    else:
        reply['message'] = 'The key is registered'
        reply['status'] = 200
        
    return pretty_dump(reply)

#-----------------------------------
@app.route('/namecoind/name_scan')
def namecoind_name_scan():
    
    start_name = request.args.get('start_name')     
    if start_name == None:
        start_name = "#"

    max_returned = request.args.get('max_returned')
    if max_returned == None:
        max_returned = 500
    else:
        max_returned = int(max_returned)

    info = json.dumps(namecoind.name_scan(start_name, max_returned))
    return pretty_dump(info)


#-----------------------------------
#helper function for name_show
def get_value(input_key):

    reply = {}

    max_returned = 1

    value = namecoind.name_scan(input_key, max_returned)

    for i in value:
        if(i['name'] == input_key):

            for key in i.keys():
                if(key == 'value'):
                    try:
                        reply[key] = json.loads(i[key])
                    except:
                        reply[key] = json.loads('{}')
                else:
                    reply[key] = i[key]

    return pretty_dump(reply)

#-----------------------------------
@app.route('/namecoind/name_show')
def namecoind_name_show():
    
    key = request.args.get('key')

    if key == None:
        return error_reply("No key given")

    return get_value(key)

#-----------------------------------
@app.route('/namecoind/onename_show')
#@requires_auth
def namecoind_onename_show():
    
    username = request.args.get('username')

    if username == None:
        return error_reply("No username given")

    if not username.startswith('u/'):
        username = 'u/' + username

    return get_value(username)

#-----------------------------------
#helper function
def unlock_wallet(passphrase, timeout = 10):

    info = namecoind.walletpassphrase(passphrase, timeout, True)
    return info             #info will be True or False

#-----------------------------------
@app.errorhandler(500)
def internal_error(error):

    reply = {}
    return pretty_dump(reply)
    
#-----------------------------------

if __name__ == '__main__':
    
    entered_passphrase = getpass.getpass('Enter passphrase: ')

    app.run(host=DEFAULT_HOST, port=DEFAULT_PORT,debug=DEBUG)