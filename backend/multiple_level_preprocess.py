# Copyright 2020 Google LLC
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# =============================================================================

"""Multiple-level preprocess module."""
from math import ceil
from time import time

from downsample import STRATEGIES
from level_slice import LevelSlice
from metadata import Metadata
from raw_data_processor import RawDataProcessor
import utils

DOWNSAMPLE_LEVEL_FACTOR = 100
MINIMUM_NUMBER_OF_RECORDS_LEVEL = 600
NUMBER_OF_RECORDS_PER_SLICE = 200000
PREPROCESS_DIR = 'mld-preprocess'
RAW_LEVEL_DIR = 'level0'
UNIX_TIMESTAMP_LENGTH = 16

TEST_FILENAME = 'DMM_result_multiple_channel.csv'


class MultipleLevelPreprocess:
    """Class for multiple level preprocessing

    To retrive data in varying time span, raw data is downsampled to a set of downsample
    levels, with decreasing frequency (number of records per second).
    Aside from downsample levels, downsampled data is split into slices of constant size,
    in order to keep constant loading time.
    To manage the knowledge of each level, a metadata.json file is genereted for the entire
    file and each slice, including names, start time of slice, start, end, and frequency, etc.
    Metadata is saved in json format, and one for raw data, one for each level.
    Example metadata for raw:
    {

        "start": 1565201629231030,
        "end": 1565201659080140,
        "raw_number": 731,
        "raw_file": "DMM_result_multiple_channel.csv",
        "levels": {
            "names": ["level0"],
            "level0": {
            "names": ["level0/s0.csv"],
            "frequency": 2.4489842410711743e-5,
            "number": 731
            }
        }
    }
    Example metadata for one level:
    {"level1/s0.csv": 1596831217804342, "level1/s1.csv": 1596831304045319}
    """

    def __init__(self, file_path, root_dir=PREPROCESS_DIR, preprocess_bucket=None, raw_bucket=None):
        """Initializes preprocess object.

        Args:
            root_dir: A string that represents the folder containing all preprocess files.
            file_path: A string that represents the path to raw data.
            preprocess_bucket: A GCP bucket object for preprocess files.
            raw_bucket: A GCP bucket object for raw files.
        """
        self._rawfile = file_path
        self._preprocess_bucket = preprocess_bucket
        self._raw_bucket = raw_bucket

        original_file_name = utils.get_file_name(file_path)
        self._preprocess_dir = '/'.join([root_dir, original_file_name])

    def is_preprocessed(self):
        """Returns if the raw data is preprocessed.

        Returns:
            A boolean indicating if the raw file is preprocessed.
        """
        metadata = Metadata(self._preprocess_dir,
                            bucket=self._preprocess_bucket)
        error = metadata.load()
        if error is None:
            return True
        return False

    def multilevel_inference(self, strategy, number_records, timespan_start, timespan_end):
        """Gets the records in given timespan, downsample the fetched data with
            given strategy if needed.

        Read the records and downsample the records to be within number_records.
        Optional arguments start and end to specify a timespan in which records must be laid.

        Args:
            strategy: A string representing a downsampling strategy.
            number_records: An interger representing number of records to return.
            timespan_start: An integer representing the timestamp in microseconds
                of the start of timespan.
            timespan_end: An integer representing the timestamp in microseconds
                of the end of timespan.

        Returns:
            A list of downsampled data in the given file, and precision for this result.
            Example:
                [
                    {
                        'name':'sys',
                        'data':[
                            [time,power],
                            [time,power]
                        ]},
                    {
                        'name': 'channel2',
                        'data': [
                            [time,power]
                        ]
                    }
                ]
        """
        self._metadata = Metadata(
            self._preprocess_dir, bucket=self._preprocess_bucket)
        self._metadata.load()

        if timespan_start is None:
            timespan_start = self._metadata['start']
        if timespan_end is None:
            timespan_end = self._metadata['end']

        if timespan_start > self._metadata['end'] or timespan_end < self._metadata['start']:
            return []

        required_frequency = number_records / (timespan_end - timespan_start)

        # Finds Downsample Level.
        target_level_index = self._binary_search(
            [self._metadata['levels'][level_name]['frequency']
             for level_name in self._metadata['levels']['names']],
            required_frequency, True)

        target_level = self._metadata['levels'][self._metadata['levels']
                                                ['names'][target_level_index]]

        level_metadata = Metadata(
            self._preprocess_dir, strategy, utils.get_level_name(
                target_level_index), bucket=self._preprocess_bucket)
        level_metadata.load()
        first_slice = self._binary_search([level_metadata[single_slice]
                                           for single_slice in target_level['names']],
                                          timespan_start)
        last_slice = self._binary_search([level_metadata[single_slice]
                                          for single_slice in target_level['names']],
                                         timespan_end)
        target_slices_names = target_level['names'][first_slice:last_slice+1]
        target_slice_paths = [utils.get_slice_path(
            self._preprocess_dir,
            utils.get_level_name(target_level_index),
            single_slice, strategy) for single_slice in target_slices_names]

        # Reads records and downsamples.
        target_slices = LevelSlice(
            filenames=target_slice_paths, bucket=self._preprocess_bucket)
        target_slices.read_slices(timespan_start, timespan_end)
        number_target_records = target_slices.get_records_count()

        target_slices.downsample(strategy, max_records=number_records)
        downsampled_data = target_slices.format_response()
        number_result_records = target_slices.get_records_count()

        if number_target_records == 0:
            precision = 0
        else:
            precision = number_result_records / \
                number_target_records * \
                (target_level['number']/self._metadata['raw_number'])
        return downsampled_data, precision

    def multilevel_preprocess(self,
                              number_per_slice,
                              downsample_level_factor,
                              minimum_number_level):
        """Multiple level downsampling entry point.

        Downsamples the raw data from given filename with each of the strategy,
        in a hierarchical fashion.
        Level0 is shared across all strategies, which contains slices of raw data.
        Level1 and Level1+ is from downsampling on level0 and the level prior to this one,
        and each strategy keeps its own levels.

        Args:
            number_per_slice: An int that represents number of records for one slice.
            downsample_level_factor: An int that represents downsample factor between levels.
                (e.g. factor=100, level1 has 100x less data than level0)
            minimum_number_level: An int that represents the minimum number of records for a level.
        """
        self._number_per_slice = number_per_slice
        self._downsample_level_factor = downsample_level_factor
        self._minimum_number_level = minimum_number_level
        self._metadata = Metadata(
            self._preprocess_dir, bucket=self._preprocess_bucket)
        self._metadata['raw_file'] = self._rawfile
        self._metadata['levels'] = dict()

        start = time()
        self._raw_preprocess(number_per_slice)
        utils.warning(('raw time is: ', time()-start))

        for strategy in STRATEGIES:
            start = time()
            self._preprocess_single_startegy(strategy)
            utils.warning((strategy, ' time is: ', time()-start))
        self._metadata.save()

    def _raw_preprocess(self, number_per_slice):
        """Splits raw data into slices. keep start time of each slice in a json file.

        Args:
            number_per_slice: An int of records to keep for each slice.
        """
        raw_slice_metadata = Metadata(
            self._preprocess_dir, strategy=None, level=RAW_LEVEL_DIR,
            bucket=self._preprocess_bucket)
        raw_data = RawDataProcessor(
            self._metadata['raw_file'], number_per_slice, self._raw_bucket)

        slice_index = 0
        raw_start_times = list()
        record_count = 0
        timespan_start = timespan_end = -1
        while raw_data.readable():
            slice_name = utils.get_slice_path(
                self._preprocess_dir, RAW_LEVEL_DIR, utils.get_slice_name(slice_index))
            level_slice = LevelSlice(
                slice_name, bucket=self._preprocess_bucket)
            raw_slice = raw_data.read_next_slice()
            level_slice.save(raw_slice)
            raw_start_times.append(raw_slice[0][0])

            slice_index += 1
            record_count += len(raw_slice)
            if timespan_start == -1:
                timespan_start = raw_slice[0][0]
            timespan_end = raw_slice[-1][0]

        self._metadata['raw_number'] = record_count
        self._metadata['start'] = timespan_start
        self._metadata['end'] = timespan_end

        levels, level_names = self._get_levels_metadata(
            record_count, timespan_end-timespan_start)
        self._metadata['levels']['names'] = level_names
        for name, level in zip(level_names, levels):
            self._metadata["levels"][name] = level
        for index, raw_slice_start in enumerate(raw_start_times):
            raw_slice_metadata[self._metadata['levels']
                               [RAW_LEVEL_DIR]['names'][index]] = raw_slice_start
        raw_slice_metadata.save()

    def _preprocess_single_startegy(self, strategy):
        """Downsamples given data by the defined levels and strategy.

        Preprocesses the raw data with the given downsanpling startegy.
        Raw data is downsampeld to a set of levels of different downsample rate,
        data of each level broken down to small slices of constant size.
        Number of levels is determined by if number of records in the highest level
        reaches minimum_number_level.
        Info regarding levels and slices is kept in a metadata json file.

        Args:
            strategy: A string representing a downsampling strategy.
        """
        if len(self._metadata['levels']['names']) <= 1:
            return
        prev_level = self._metadata['levels']['names'][0]
        for curr_level in self._metadata['levels']['names'][1:]:
            level_metadata = Metadata(
                self._preprocess_dir, strategy, curr_level, bucket=self._preprocess_bucket)
            self._single_level_downsample(
                strategy, prev_level, curr_level, level_metadata)
            level_metadata.save()
            prev_level = curr_level

    def _get_levels_metadata(self, raw_number_records, duration):
        """Gets level meta infomation for each level.

        Args:
            raw_number_records: An int that represents the number of raw records.
            duration: An int that represents duration of the power test which produced
                the DMM power data.

        Returns:
            A tuple of length 2, that contains level meta info ojbject and level names.
        """
        assert self._number_per_slice > 0
        assert self._downsample_level_factor > 1

        levels = []
        level_names = []
        number_records = raw_number_records
        index = 0
        while index == 0 or number_records >= self._minimum_number_level:
            frequency = number_records / duration
            level_name = utils.get_level_name(index)
            number_slices = ceil(
                number_records / self._number_per_slice)
            slice_names = ['/'.join([level_name, utils.get_slice_name(
                index)]) for index in range(number_slices)]
            level = {
                "names": slice_names,
                "frequency": frequency,
                "number": number_records
            }
            levels.append(level)
            level_names.append(level_name)

            index += 1
            number_records = number_records // self._downsample_level_factor
        return levels, level_names

    def _single_level_downsample(self, strategy, prev_level, curr_level, level_metadata):
        """Downsamples for one single level.

        Args:
            strategy: A string representing a downsampling strategy.
            prev_level: A string of the name of the current level.
            curr_level: A string of the name of the previous level.
            level_metadata: A metadata object for this level.

        Returns:
            A dict of metadata for the current level.
        """
        curr_slice_names = self._metadata['levels'][curr_level]['names']
        prev_slice_names = self._metadata['levels'][prev_level]['names']

        slice_index = 0
        curr_slice_path = utils.get_slice_path(self._preprocess_dir,
                                               curr_level, utils.get_slice_name(
                                                   slice_index), strategy)
        curr_level_slice = LevelSlice(curr_slice_path)

        for prev_slice_name in prev_slice_names:
            prev_slice_path = utils.get_slice_path(self._preprocess_dir,
                                                   prev_level, prev_slice_name, strategy)
            prev_level_slice = LevelSlice(
                prev_slice_path, bucket=self._preprocess_bucket)
            prev_level_slice.read()
            prev_level_downsample = prev_level_slice.downsample(
                strategy, self._downsample_level_factor)
            curr_level_slice.add_records(prev_level_downsample)
            if curr_level_slice.get_records_count() >= self._number_per_slice:
                curr_level_slice.save()
                level_metadata[curr_slice_names
                               [slice_index]] = curr_level_slice.get_first_timestamp()
                slice_index += 1
                curr_slice_path = utils.get_slice_path(self._preprocess_dir,
                                                       curr_level, utils.get_slice_name(
                                                           slice_index), strategy)
                curr_level_slice = LevelSlice(curr_slice_path)

        curr_level_slice.save()
        level_metadata[curr_slice_names
                       [slice_index]] = curr_level_slice.get_first_timestamp()
        return level_metadata

    def _binary_search(self, data_list, value, reverse=False):
        """Searches the index of the left or right element closest to the given value from the list,
        if reverse is true, the list is decreasing.

        Args:
            data_list: A list of integers.
            value: The value to be inserted.
            reverse: True if data_list is decreasing.

        Returns:
            An int of index for the result.
        """
        if not data_list:
            return -1

        left = 0
        right = len(data_list) - 1
        pivot = (left + right + 1) // 2

        while left < right:
            if reverse:
                if data_list[pivot] >= value:
                    left = pivot
                else:
                    right = pivot - 1
            else:
                if data_list[pivot] < value:
                    left = pivot
                else:
                    right = pivot - 1
            pivot = (left + right + 1) // 2
        return pivot


if __name__ == '__main__':
    from google.cloud import storage
    client = storage.Client()
    b1 = client.bucket('power-data-preprocess')
    b2 = client.bucket('power-data-raw')
    a = MultipleLevelPreprocess(TEST_FILENAME, PREPROCESS_DIR, b1, b2)
    a.multilevel_preprocess(
        NUMBER_OF_RECORDS_PER_SLICE, DOWNSAMPLE_LEVEL_FACTOR, MINIMUM_NUMBER_OF_RECORDS_LEVEL)
