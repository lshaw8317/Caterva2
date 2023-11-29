###############################################################################
# Caterva2 - On demand access to remote Blosc2 data repositories
#
# Copyright (c) 2023 The Blosc Developers <blosc@blosc.org>
# https://www.blosc.org
# License: GNU Affero General Public License v3.0
# See LICENSE.txt for details about copyright and rights to use.
###############################################################################

import contextlib
import json
import logging
import pathlib

# Requirements
import asyncio
import blosc2
from fastapi import FastAPI
import httpx
import uvicorn

# Project
import models
import utils


logger = logging.getLogger('sub')

# Configuration
broker = None
cache = None
nworkers = 100

# World view, propagated through the broker, with information from the publihsers
roots = {} # name: <Root>

subscribed = {} # name/path: <PubSubClient>

queue = asyncio.Queue()

def download_chunk(host, name, nchunk, schunk):
    with httpx.stream('GET', f'http://{host}/api/download/{name}?{nchunk=}') as resp:
        buffer = []
        for chunk in resp.iter_bytes():
            buffer.append(chunk)
        chunk = b''.join(buffer)
        schunk.insert_chunk(nchunk, chunk)

async def worker(queue):
    while True:
        name = await queue.get()
        with utils.log_exception(logger, 'Download failed'):
            urlpath = cache / name
            urlpath.parent.mkdir(exist_ok=True, parents=True)

            dataset = datasets[name]
            src, name = name.split('/', 1)
            host = publishers[src].http

            suffix = urlpath.suffix
            if suffix == '.b2nd':
                metadata = models.Metadata(**dataset)
                try:
                    array = blosc2.open(str(urlpath))
                except FileNotFoundError:
                    utils.init_b2nd(urlpath, metadata)
                    array = blosc2.open(str(urlpath))

                for nchunk in range(metadata.schunk.nchunks):
                    download_chunk(host, name, nchunk, array.schunk)
            elif suffix == '.b2frame':
                metadata = models.SChunk(**dataset)
                try:
                    schunk = blosc2.open(str(urlpath))
                except FileNotFoundError:
                    utils.init_b2frame(urlpath, metadata)
                    schunk = blosc2.open(str(urlpath))

                for nchunk in range(metadata.nchunks):
                    download_chunk(host, name, nchunk, schunk)
            else:
                with urlpath.open('wb') as file:
                    with httpx.stream('GET', f'http://{host}/api/download/{name}') as resp:
                        for chunk in resp.iter_bytes():
                            file.write(chunk)

        queue.task_done()


async def new_root(data, topic):
    logger.info(f'NEW root {topic} {data=}')
    root = models.Root(**data)
    roots[root.name] = root

async def updated_dataset(data, topic):
    logger.info(f'Updated dataset {topic} {data=}')

#
# The "database" is used to persist the subscriber state, so it survives restarts
#

database = None

class Database:

    def __init__(self, path):
        self.path = path

        if path.exists():
            self.load()
        else:
            self.init()

    def init(self):
        self.data = {
            'following': [], # List of datasets we are subscribed to
        }
        self.save()

    def load(self):
        with self.path.open() as file:
            self.data = json.load(file)

    def save(self):
        with self.path.open('w') as file:
            json.dump(self.data, file)

    def __getitem__(self, key):
        return self.data[key]

    def __setitem__(self, key, value):
        self.data[key] = value


#
# Internal API
#

def follow(name: str):
    root = roots.get(name)
    if root is None:
        errors = {}
        errors[name] = 'This dataset does not exist in the network'
        return errors

    data = utils.get(f'http://{root.http}/api/list')
    for path in data:
        print(path)

#   # Initialize the dataset in the filesystem (cache)
#   urlpath = cache / name
#   if not urlpath.exists():
#       suffix = urlpath.suffix
#       dataset = datasets[name]
#       if suffix == '.b2nd':
#           metadata = models.Metadata(**dataset)
#           utils.init_b2nd(urlpath, metadata)
#       elif suffix == '.b2frame':
#           metadata = models.SChunk(**dataset)
#           utils.init_b2frame(urlpath, metadata)

#   # Subscribe to changes in the dataset
#   if name not in subscribed:
#       client = utils.start_client(f'ws://{broker}/pubsub')
#       client.subscribe(name, updated_dataset)
#       subscribed[name] = client

#   following = database['following']
#   if name not in following:
#       following.append(name)
#       database.save()

#   # Get the publisher hostname and port for later downloading
#   src = name.split('/', 1)[0]
#   if src not in publishers:
#       url = f'http://{broker}/api/publishers/{src}'
#       publishers[src] = utils.get(url, model=models.Root)


#
# HTTP API
#

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize roots from the broker
    data = utils.get(f'http://{broker}/api/roots')
    for name, data in data.items():
        root = models.Root(**data)
        roots[root.name] = root

    # Follow the @new channel to know when a new root is added
    client = utils.start_client(f'ws://{broker}/pubsub')
    client.subscribe('@new', new_root)

    # Resume following
    for path in cache.iterdir():
        if path.is_dir():
            follow(path.name)

    # Start workers
    tasks = []
    for i in range(nworkers):
        task = asyncio.create_task(worker(queue))
        tasks.append(task)

    yield

    # Disconnect from worker
    await utils.disconnect_client(client)

    # Cancel worker tasks
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

app = FastAPI(lifespan=lifespan)

@app.get('/api/roots')
async def get_roots():
    return sorted(roots)

@app.get('/api/list/{name}')
async def get_list(name: str):
    root = roots.get(name)
    if root is None:
        utils.raise_not_found(f'{name} not known by the broker')

    rootdir = cache / root.name
    if not rootdir.exists():
        utils.raise_not_found(f'not subscribed to {name}')

    for path, relpath in utils.walk_files(rootdir):
        yield relpath

@app.post('/api/subscribe')
async def post_subscribe(name: str):
    root = roots.get(name)
    if root is None:
        utils.raise_not_found(f'{name} not known by the broker')

    return follow(name)

"""
@app.get('/api/info')
async def get_info():
    info = {}

    for path, relpath in utils.walk_files(cache, exclude={'db.json'}):
        stat = path.stat()
        info[relpath] = {'mtime': stat.st_mtime, 'follow': False}

    for path in database['following']:
        path_info = info.get(path)
        if path_info is None:
            info[path] = {'mtime': None, 'follow': True}
        else:
            info[path]['follow'] = True

    return info


@app.post('/api/unfollow')
async def post_unfollow(delete: list[str]):
    for name in delete:
        client = subscribed.pop(name, None)
        await utils.disconnect_client(client)

@app.post("/api/download")
async def post_download(datasets: list[str]):
    for dataset in datasets:
        queue.put_nowait(dataset)
"""


#
# Command line interface
#

if __name__ == '__main__':
    parser = utils.get_parser(broker='localhost:8000', http='localhost:8002')
    args = utils.run_parser(parser)

    # Global configuration
    broker = args.broker

    # Create cache directory
    cache = pathlib.Path('cache').resolve()
    cache.mkdir(exist_ok=True)

    # Open or create database file
    database = Database(cache / 'db.json')

    # Run
    host, port = args.http
    uvicorn.run(app, host=host, port=port)
