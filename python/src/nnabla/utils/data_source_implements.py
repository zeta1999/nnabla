# Copyright (c) 2017 Sony Corporation. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

'''data_source_implements
'''


from collections import OrderedDict
from time import sleep
import csv
import numpy
import os
import threading

from .data_source import DataSource
from .data_source_loader import FileReader, load
from nnabla.logger import logger
from nnabla.utils.communicator_util import current_communicator


class SimpleDataSource(DataSource):
    '''SimpleDataSource

    Get data from user defined function.

    '''

    def _get_data(self, position):
        return self._load_func(self._order[position])

    def reset(self):
        pass

    def __init__(self, load_func, num_examples, shuffle=False, rng=None):
        super(SimpleDataSource, self).__init__(shuffle=shuffle, rng=rng)
        super(SimpleDataSource, self).reset()
        self._load_func = load_func
        self._size = num_examples
        self._variables = ['x' + str(x)
                           for x in range(len(self._load_func(0)))]
        if shuffle:
            self._order = list(
                self._rng.permutation(list(range(self._size))))
        else:
            self._order = list(range(self._size))


class CacheDataSource(DataSource):
    '''
    Get data from file cache directly.
    '''

    def _get_next_data(self, filename, retry=1):
        if retry > 10:
            logger.log(99, '_get_next_data() retry count over give up.')
            raise
        next_data = {}
        with self._filereader.open_cache(filename) as cache:
            for k, v in cache.items():
                next_data[k] = v.value

            if current_communicator():
                if set(self._variables) != set(next_data.keys()):
                    logger.log(99, '_get_data() fails at worker {} retrying count {}/10.'.format(
                        current_communicator().rank, retry))
                    sleep(0.01)
                    self._get_next_data(filename, retry+1)
        self._next_data = next_data

    def _get_data(self, position):

        with self._thread_lock:
            self._position = position
            try:
                filename, index = self._order[position]
            except:
                print(len(self._order), position)
            if filename != self._current_filename:

                if self._use_thread:
                    if self._next_thread is None:
                        self._get_next_data(filename)
                    else:
                        self._next_thread.join()

                    self._next_thread = None
                    self._current_data = self._next_data
                    self._current_filename = filename

                    next_pos = position
                    next_filename = filename
                    while True:
                        next_filename, next_index = self._order[next_pos]
                        next_pos += 1
                        if next_filename != filename:
                            self._next_thread = threading.Thread(
                                target=self._get_next_data, args=(next_filename, ))
                            self._next_thread.start()
                            break
                        if next_pos >= self._size:
                            break
                else:
                    self._get_next_data(filename)
                    self._current_data = self._next_data
                    self._current_filename = filename

            data = [self._current_data[v][index] for v in self.variables]

            if self._normalize:
                data = [d.astype(numpy.float32) * (1.0 / 255.0)
                        if d.dtype == numpy.uint8 else d for d in data]
        return data

    def initialize_cache_files(self, filename):
        length = -1
        with self._filereader.open_cache(filename) as cache:

            # Check variables.
            if self._variables is None:
                self._variables = list(cache.keys())
            else:
                if current_communicator():
                    if not set(self._variables) == set(cache.keys()):
                        logger.log(99, 'Error at worker {} {} {}'.format(
                            current_communicator().rank, set(self._variables), set(cache.keys())))
                        raise

            for k, v in cache.items():
                if length < 0:
                    length = len(v)
                else:
                    assert(length == len(v))
            self._cache_files.append((filename, length))
            logger.info('{} {}'.format(filename, length))

    def __init__(self, cachedir, shuffle=False, rng=None, normalize=False, use_thread=True):
        super(CacheDataSource, self).__init__(shuffle=shuffle, rng=rng)

        self._current_data = {}
        self._current_filename = None

        self._cachedir = cachedir
        self._normalize = normalize
        self._filereader = FileReader(self._cachedir)
        self._filenames = self._filereader.listdir()

        self._generation = -1
        self._cache_files = []

        for filename in self._filenames:
            self.initialize_cache_files(filename)

        logger.info('{}'.format(len(self._cache_files)))

        self._use_thread = use_thread
        self._thread_lock = threading.Lock()
        if self._use_thread:
            self._next_thread = None

        self.reset()

    def reset(self):
        with self._thread_lock:
            super(CacheDataSource, self).reset()

            self._order = []

            if self._shuffle:
                for i in list(self._rng.permutation(list(range(len(self._cache_files))))):
                    filename, length = self._cache_files[i]
                    for j in list(self._rng.permutation(list(range(length)))):
                        self._order.append((filename, j))
            else:
                for i in range(len(self._cache_files)):
                    filename, length = self._cache_files[i]
                    for j in range(length):
                        self._order.append((filename, j))

            self._current_data = {}
            self._current_filename = None
            if self._use_thread:
                self._next_thread = None

            self._size = len(self._order)
            self._generation += 1


class CsvDataSource(DataSource):
    '''
    '''

    def _process_header(self, row):
        self._variables_dict = OrderedDict()
        self._columns = []
        for column, column_value in enumerate(row):

            # Analyze header "NAME[__INDEX][:LABELNAME]"
            # TODO: use regex instead of split....
            try:
                variable_with_index, label = column_value.split(':', 1)
            except:
                label = None
                variable_with_index = column_value
            try:
                variable, index = variable_with_index.split('__', 1)
            except:
                variable = variable_with_index
                index = None

            self._columns.append((variable, index, label))
            if index is None:
                self._variables_dict[variable] = {
                    'label': label, 'value': None}
            else:
                if variable not in self._variables_dict:
                    self._variables_dict[variable] = []
                self._variables_dict[variable].append(
                    {'label': label, 'value': None})

    def _process_row(self, row):
        values = OrderedDict()
        if len(row) == len(self._columns):
            for column, column_value in enumerate(row):
                variable, index, label = self._columns[column]
                if index is None:
                    values[variable] = self._get_value(
                        column_value, is_vector=True)
                else:
                    if variable not in values:
                        values[variable] = []
                    values[variable].append(self._get_value(column_value))
        return values.values()

    def _get_value(self, value, is_vector=False):
        try:
            if is_vector:
                value = [float(value)]
            else:
                value = float(value)
        except:
            ext = (os.path.splitext(value)[1]).lower()
            with self._filereader.open(value) as f:
                value = load(ext)(f, normalize=self._normalize)
        return value

    def _get_data(self, position):
        return tuple(self._process_row(self._rows[self._order[position]]))

    def __init__(self, filename, shuffle=False, rng=None, normalize=False):
        super(CsvDataSource, self).__init__(shuffle=shuffle, rng=rng)
        self._filename = filename
        self._normalize = normalize

        # Store contents of CSV file into the self._rows list.
        self._generation = -1
        self._rows = []
        self._filereader = FileReader(self._filename)
        with self._filereader.open() as f:
            csv_lines = [x.decode('utf-8') for x in f.readlines()]
            csvreader = csv.reader(csv_lines)
            first_line = True
            for row in csvreader:
                if first_line:
                    self._process_header(row)
                    first_line = False
                else:
                    self._rows.append(row)
                    self._size += 1
        self._order = list(range(self._size))
        self._variables = tuple(self._variables_dict.keys())
        self.reset()

    def reset(self):
        if self._shuffle:
            logger.debug('Shuffle start.')
            self._order = list(
                self._rng.permutation(list(range(self._size))))
            logger.debug('Shuffle end.')
        self._generation += 1
        super(CsvDataSource, self).reset()


class ConcatDataSource(DataSource):
    '''ConcatDataSource

    Wrapper DataSource for Multiple DataSources.

    '''

    def __init__(self, data_source_list, shuffle=True, rng=None):
        super(ConcatDataSource, self).__init__(shuffle=shuffle, rng=rng)
        self._data_sources = data_source_list

        self._sw_points = list(map(
            lambda s: sum(
                [x.size for x in data_source_list[:data_source_list.index(s) + 1]]),
            data_source_list))  # Switching DataSource index

        self._size = self._sw_points[-1]
        self._variables = data_source_list[0].variables
        self.reset()

    def _get_data(self, position):
        idx = self._indexes[position]
        for i, data_bound in enumerate(self._sw_points):
            if idx < data_bound:
                _idx = idx - self._sw_points[i - 1] if i > 0 else idx
                return self._data_sources[i]._get_data(_idx)
        return None

    def reset(self):
        # reset method initilize self._indexes
        if self._shuffle:
            self._indexes = self._rng.permutation(self._size)
        else:
            self._indexes = numpy.arange(self._size)
        super(ConcatDataSource, self).reset()
