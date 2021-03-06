#!/usr/bin/env python

import roslib; roslib.load_manifest('ros_datacentre')
import rospy
import sys
import os
import collections
import json

import ros_datacentre.util

from ros_datacentre.srv import *
from std_srvs.srv import *
import rosparam

if not ros_datacentre.util.check_for_pymongo():
    sys.exit(1)

import pymongo


class ConfigManager(object):
    def __init__(self):
        rospy.init_node("config_manager")
        rospy.on_shutdown(self._on_node_shutdown)

        if not ros_datacentre.util.wait_for_mongo():
            sys.exit(1)
        
        self._mongo_client = pymongo.MongoClient(rospy.get_param("datacentre_host","localhost"),
                                                 int(rospy.get_param("datacentre_port")))

        # Load the default settings from the defaults/ folder
        path = os.path.join(roslib.packages.get_pkg_dir('ros_datacentre'),"defaults")
        files = os.listdir(path)
        defaults=[]  # a list of 3-tuples, (param, val, originating_filename)
        def flatten(d, c="", f_name="" ):
            l=[]
            for k, v in d.iteritems():
                if isinstance(v, collections.Mapping):
                    l.extend(flatten(v,c+"/"+k, f_name))
                else:
                    l.append((c+"/"+k, v, f_name))
            return l
                             
        for f in files:
            if not f.endswith(".yaml"):
                continue
            params = rosparam.load_file(os.path.join(path,f))
            rospy.loginfo("Found default parameter file %s" % f)
            for p, n in params:
                defaults.extend(flatten(p,c="",f_name=f))

        # Copy the defaults into the DB if not there already
        defaults_collection = self._mongo_client.config.defaults
        for param,val,filename in defaults:
            existing = defaults_collection.find_one({"path":param})
            if existing is None:
                rospy.loginfo("New default parameter for %s"%param)
                defaults_collection.insert({"path":param,
                                            "value":val,
                                            "from_file":filename})
            elif existing["from_file"]!=filename:
                rospy.logerr("Two defaults parameter files have the same key:\n%s and %s, key %s"%
                             (existing["from_file"],filename,param))
                # Delete the entry so that it can be fixed...
                defaults_collection.remove(existing)
                rospy.signal_shutdown("Default parameter set error")
            elif existing["value"]!=val:
                rospy.loginfo("Updating stored default for %s"%param)
                defaults_collection.update(existing,{"$set":{"value":val}})
        
                
        # Load the settings onto the ros parameter server
        defaults_collection = self._mongo_client.config.defaults
        local_collection = self._mongo_client.config.local
        for param in defaults_collection.find():
            name=param["path"]
            val=param["value"]
            if local_collection.find_one({"path":name}) is None:
                rospy.set_param(name,val)
        for param in local_collection.find():
            name=param["path"]
            val=param["value"]
            rospy.set_param(name,val)

        
        # Advertise ros services for parameter setting / getting
        self._getparam_srv = rospy.Service("/config_manager/get_param",
                                           GetParam,
                                           self._getparam_srv_cb)
        self._setparam_srv = rospy.Service("/config_manager/set_param",
                                           SetParam,
                                           self._setparam_srv_cb)
        self._saveparam_srv = rospy.Service("/config_manager/save_param",
                                           SetParam,
                                           self._saveparam_srv_cb)
        
        #self._list_params()
        
        # Start the main loop
        rospy.spin()

    """
    debug function, prints out all parameters known
    """
    def _list_params(self):
        print "#"*10
        print "Defaults:"
        print
        for param in self._mongo_client.config.defaults.find():
            name=param["path"]
            val=param["value"]
            filename=param["from_file"]
            print name, " "*(30-len(name)),val," "*(30-len(str(val))),filename
        print
        
        
    def _on_node_shutdown(self):
        self._mongo_client.disconnect()

    # Could just use the ros parameter server to get the params
    # but one day might not back onto the parameter server...
    def _getparam_srv_cb(self,req):
        response = GetParamResponse()
        config_db = self._mongo_client.config
        value = config_db.local.find_one({"path":req.param_name})
        if value is None:
            value = config_db.defaults.find_one({"path":req.param_name})
            if value is None:
                response.success=False
                return response
        response.success=True
        response.param_value=str(value["value"])
        return response

    """
    Set the local site-specific parameter.
    """
    def _setparam_srv_cb(self,req):
        print ("parse json")
        new = json.loads(req.param)
        if not (new.has_key("path") and new.has_key("value")):
            rospy.logerr("Trying to set parameter but not giving full spec")
            return SetParamResponse(False)
        config_db_local = self._mongo_client.config.local
        value = config_db_local.find_one({"path":new["path"]})
        if value is None:
            # insert it
            config_db_local.insert(new)
        else:
            # update it
            config_db_local.update(value,{"$set":new})
            pass
        return SetParamResponse(True)

    #This will take the current value from the rosparam server and save it into the DB
    def _saveparam_srv_cb(self,req):
        if not rospy.has_param(req.param):
            rospy.logerr("Trying to set parameter but not giving full spec")
            return SetParamResponse(False)
        val=rospy.get_param(req.param)
        if type(val) is str:
            mystring='{"path":"'+ str(req.param) +'","value":"'+ val +'"}'
        else:
            mystring='{"path":"'+ str(req.param) +'","value":'+ str(val) +'}'
        new = json.loads(mystring)
        config_db_local = self._mongo_client.config.local
        value = config_db_local.find_one({"path":new["path"]})
        if value is None:
            # insert it
            config_db_local.insert(new)
        else:
            # update it
            config_db_local.update(value,{"$set":new})
            pass
        return SetParamResponse(True)
   

if __name__ == '__main__':
    server = ConfigManager()

