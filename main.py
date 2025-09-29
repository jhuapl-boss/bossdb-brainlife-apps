#! /usr/bin/env python3


import json
import igneous

from taskqueue import LocalTaskQueue
import igneous.task_creation as tc
# Load a JSON file with a data pointer, parameters, and output location
# JSON created at run time that user chose on the app interface
# Datatype specifies the "cloudpath", e.g. "s3://bossdb-open-data/{}/{}/{}"

# Next steps:
# local test with custom written json (use kasthuri?) and example data 

if __name__ == "__main__":
    print("Hello, BrainLife!")
    
    # load inputs from config.json
    with open('config.json') as config_json:
        config = json.load(config_json)
    
    print(config['params'])


    # Mesh on 8 cores, use True to use all cores
    cloudpath = config['cloudpath']
    tq = LocalTaskQueue(parallel=True)

    tasks = tc.create_meshing_tasks(cloudpath, mip=config['mip'], shape=tuple(config['shape']))
    tq.insert(tasks)
    tq.execute()
    tasks = tc.create_mesh_manifest_tasks(cloudpath)
    tq.insert(tasks)
    tq.execute()
    print("Done!")