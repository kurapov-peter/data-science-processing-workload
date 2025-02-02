# Licensed to Modin Development Team under one or more contributor license agreements.
# See the NOTICE file distributed with this work for additional information regarding
# copyright ownership.  The Modin Development Team licenses this file to you under the
# Apache License, Version 2.0 (the "License"); you may not use this file except in
# compliance with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software distributed under
# the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF
# ANY KIND, either express or implied. See the License for the specific language
# governing permissions and limitations under the License.

import argparse
import abc

import os
import numpy as np
try:
    import modin.pandas as pd
except ImportError:
    import pandas as pd
from numpy.random import default_rng, SeedSequence

seed = 42


class DatasetGenerator(abc.ABC):
    def __init__(self, output_file_name: str, reuse: bool, parallel: bool, num_cpus: int):
        self._output_file_name = output_file_name
        self._reuse = reuse
        self._parallel = parallel
        self._num_cpus = num_cpus

    @abc.abstractmethod
    def generate_check_args(self, **kwargs):
        pass

    @staticmethod
    def _generate_int(rnd, records: int, series_params: tuple):
        low, high = series_params
        return rnd.integers(low=low, high=high, size=records, endpoint=True)

    @staticmethod
    def _generate_float(rnd, records: int, series_params: tuple):
        low, high = series_params
        return rnd.uniform(low=low, high=high, size=records)

    @staticmethod
    def _generate_datetime(rnd, records: int, series_params: tuple):
        low, high = series_params
        time_delta = high - low
        int_delta_seconds = time_delta / np.timedelta64(1, "s")
        int_series = rnd.integers(
            low=0, high=int_delta_seconds, size=records, endpoint=True
        )
        time_delta_series = int_series.astype(np.timedelta64)
        return time_delta_series + low

    @staticmethod
    def _generate_categoricals(rnd, records: int, series_params: tuple):
        return rnd.choice(series_params, size=records)

    @staticmethod
    def _generate_object(rnd, records: int, series_params: tuple):
        return np.nan


    _generators = {
        "int64": _generate_int.__func__,
        "int32": _generate_int.__func__,
        "float64": _generate_float.__func__,
        "float32": _generate_float.__func__,
        "datetime64[ns]": _generate_datetime.__func__,
        "categorical": _generate_categoricals.__func__,
        "object": _generate_object.__func__,
    }

    @classmethod
    def _generate_series(cls, params: list):
        rnd, name, type_name, records, series_params = params
        return name, pd.Series(
            cls._generators[type_name](rnd, records, series_params), name=name
        )

    def _generate_data(self, fields: dict, records_number: int):
        generators = SeedSequence(seed).spawn(len(fields))
        map_args = [
            (
                default_rng(generators[i]),
                column[0],
                column[1][0],
                records_number,
                column[1][1:],
            )
            for i, column in enumerate(fields.items())
        ]

        if self._parallel:
            import ray

            if not ray.is_initialized():
                ray_ver = [int(x) for x in ray.__version__.split(".")]
                if ray_ver[0] < 1 or ray_ver[0] == 1 and ray_ver[1] <= 6:
                    # Workaround for ray-1.6.0 problem with runtime_env parameter
                    ray.init(num_cpus=self._num_cpus)
                else:
                    ray.init(num_cpus=self._num_cpus, runtime_env={"env_vars": {"__MODIN_AUTOIMPORT_PANDAS__": "1"}})

            @ray.remote
            def remote_map(f, obj):
                return f(obj)

            data = dict(
                ray.get([remote_map.remote(self._generate_series, x) for x in map_args])
            )
        else:
            data = {}
            for arg in map_args:
                key, value = self._generate_series(list(arg))
                data[key] = value

        return data

    def _generate_and_write_data(
        self, fields: dict, output_file_name: str, records_number: int
    ):
        data = self._generate_data(fields, records_number)
        print("Writing output to", output_file_name)
        pd.DataFrame(data).to_csv(output_file_name, index=False)

    @staticmethod
    def _split_range_into_random_parts(range_max, num_parts, min_size, max_size):
        parts = []
        rnd = default_rng(SeedSequence(seed))
        current = 0
        for p in range(num_parts):
            remaining = range_max - current
            avg_remaining_size = round(remaining / (num_parts - p))
            delta = round((max_size + min_size) / 2) - avg_remaining_size
            low = min_size
            high = max_size
            if delta > 0:
                high = max(high - delta, low)
            else:
                low = min(low - delta, high)
            size = rnd.integers(low, high, endpoint=True)
            parts.append(size)
            current += size

        return parts


class TaxiGenerator(DatasetGenerator):
    _fields = {
        "trip_id": ("int64", 1, 1464785771),
        "vendor_id": ("object", 0, 0),
        "pickup_datetime": (
            "datetime64[ns]",
            np.datetime64("2013-01-01 00:00:00"),
            np.datetime64("2015-12-31 23:59:59"),
        ),
        "dropoff_datetime": (
            "datetime64[ns]",
            np.datetime64("2013-01-01 00:00:00"),
            np.datetime64("2015-12-31 23:59:59"),
        ),
        "store_and_fwd_flag": ("object", 0, 0),
        "rate_code_id": ("int64", 0, 252),
        "pickup_longitude": ("float64", -3509.015037, 3570.224107),
        "pickup_latitude": ("float64", -3579.139413, 3577.13555),
        "dropoff_longitude": ("float64", -3579.139413, 3460.426853),
        "dropoff_latitude": ("float64", -3579.139413, 3577.135043),
        "passenger_count": ("int64", 0, 9),
        "trip_distance": ("float64", 0, 830),
        "fare_amount": ("float64", -1430.0, 861604.49),
        "extra": ("float64", -79.0, 14000.0),
        "mta_tax": ("float64", -49.5, 250.0),
        "tip_amount": ("float64", -440.0, 3950588.8),
        "tolls_amount": ("float64", -99.99, 7999.92),
        "ehail_fee": ("float64", 0, 0),
        "improvement_surcharge": ("float64", -0.3, 137.63),
        "total_amount": ("float64", -1430.0, 3950611.6),
        "payment_type": ("object", 0, 0),
        "trip_type": ("float64", 1.0, 2.0),
        "pickup": ("object", 0, 0),
        "dropoff": ("object", 0, 0),
        "cab_type": ("categorical", "green", "yellow"),
        "precipitation": ("float64", 0.0, 5.81),
        "snow_depth": ("int64", 0, 23),
        "snowfall": ("float64", 0.0, 27.3),
        "max_temperature": ("int64", 15, 104),
        "min_temperature": ("int64", -1.0, 84.0),
        "average_wind_speed": ("float64", 0.22, 18.79),
        "pickup_nyct2010_gid": ("float64", 1.0, 2167.0),
        "pickup_ctlabel": ("float64", 1.0, 9901.0),
        "pickup_borocode": ("float64", 1.0, 5.0),
        "pickup_boroname": ("object", 0, 0),
        "pickup_ct2010": ("float64", 100.0, 990100.0),
        "pickup_boroct2010": ("float64", 1000100.0, 5990100.0),
        "pickup_cdeligibil": ("object", 0, 0),
        "pickup_ntacode": ("object", 0, 0),
        "pickup_ntaname": ("object", 0, 0),
        "pickup_puma": ("float64", 3701.0, 4114.0),
        "dropoff_nyct2010_gid": ("float64", 1.0, 2167.0),
        "dropoff_ctlabel": ("float64", 1.0, 9901.0),
        "dropoff_borocode": ("float64", 1.0, 5.0),
        "dropoff_boroname": ("object", 0, 0),
        "dropoff_ct2010": ("float64", 100.0, 990100.0),
        "dropoff_boroct2010": ("float64", 1000100.0, 5990100.0),
        "dropoff_cdeligibil": ("object", 0, 0),
        "dropoff_ntacode": ("object", 0, 0),
        "dropoff_ntaname": ("object", 0, 0),
        "dropoff_puma": ("float64", 3701.0, 4114.0),
    }

    def generate_check_args(self, **kwargs):
        records = kwargs.pop("records", None)
        assert (
            records is not None
        ), 'Parameter "--records" is required for taxi benchmark'
        print("Generating taxi")
        self.generate(records)

    def generate(self, records: int):
        if not self._reuse:
            self._generate_and_write_data(self._fields, self._output_file_name, records)
        return self._output_file_name


class CensusGenerator(DatasetGenerator):
    _fields = {
        "YEAR0": ("int64", 1970, 2010),
        "DATANUM": ("int64", 1, 4),
        "SERIAL": ("int64", 1, 4711341),
        "CBSERIAL": ("float64", 2.0, 1414542.0),
        "HHWT": ("int64", 1, 1385),
        "CPI99": ("float64", 0.764, 4.54),
        "GQ": ("int64", 0, 5),
        "QGQ": ("float64", 0.0, 5.0),
        "PERNUM": ("int64", 1, 32),
        "PERWT": ("int64", 1, 1385),
        "SEX": ("int64", 1, 2),
        "AGE": ("int64", 0, 100),
        "EDUC": ("int64", 0, 11),
        "EDUCD": ("int64", 0, 116),
        "INCTOT": ("int64", -20000, 9999999),
        "SEX_HEAD": ("float64", 1.0, 2.0),
        "SEX_MOM": ("float64", 2.0, 2.0),
        "SEX_POP": ("float64", 1.0, 1.0),
        "SEX_SP": ("float64", 1.0, 2.0),
        "SEX_MOM2": ("float64", 2.0, 2.0),
        "SEX_POP2": ("float64", 1.0, 1.0),
        "AGE_HEAD": ("float64", 14.0, 100.0),
        "AGE_MOM": ("float64", 0.0, 100.0),
        "AGE_POP": ("float64", 0.0, 100.0),
        "AGE_SP": ("float64", 0.0, 100.0),
        "AGE_MOM2": ("float64", 6.0, 94.0),
        "AGE_POP2": ("float64", 1.0, 90.0),
        "EDUC_HEAD": ("float64", 0.0, 11.0),
        "EDUC_MOM": ("float64", 0.0, 11.0),
        "EDUC_POP": ("float64", 0.0, 11.0),
        "EDUC_SP": ("float64", 0.0, 11.0),
        "EDUC_MOM2": ("float64", 0.0, 11.0),
        "EDUC_POP2": ("float64", 0.0, 11.0),
        "EDUCD_HEAD": ("float64", 2.0, 116.0),
        "EDUCD_MOM": ("float64", 0.0, 116.0),
        "EDUCD_POP": ("float64", 0.0, 116.0),
        "EDUCD_SP": ("float64", 1.0, 116.0),
        "EDUCD_MOM2": ("float64", 2.0, 116.0),
        "EDUCD_POP2": ("float64", 1.0, 116.0),
        "INCTOT_HEAD": ("float64", -20000.0, 9999999.0),
        "INCTOT_MOM": ("float64", -19998.0, 9999999.0),
        "INCTOT_POP": ("float64", -20000.0, 9999999.0),
        "INCTOT_SP": ("float64", -20000.0, 9999999.0),
        "INCTOT_MOM2": ("float64", -16.0, 9999999.0),
        "INCTOT_POP2": ("float64", -10000.0, 9999999.0),
    }

    def generate_check_args(self, **kwargs):
        records = kwargs.pop("records", None)
        assert (
            records is not None
        ), 'Parameter "--records" is required for census benchmark'
        print("Generating census")
        self.generate(records)

    def generate(self, records: int):
        if not self._reuse:
            self._generate_and_write_data(self._fields, self._output_file_name, records)
        return self._output_file_name


class PlasticcGenerator(DatasetGenerator):
    _training_set_fields = {
        "mjd": ("float32", 59580.03515625, 60674.36328125),
        "passband": ("int32", 0, 5),
        "flux": ("float32", -1149388.375, 2432808.75),
        "flux_err": ("float32", 0.46375301480293274, 2234069.25),
        "detected": ("int32", 0, 1),
    }
    _test_set_fields = {
        "mjd": ("float32", 59580.03515625, 60674.36328125),
        "passband": ("int32", 0, 5),
        "flux": ("float32", -8935484.0, 13675792.0),
        "flux_err": ("float32", 0.46375301480293274, 13791667.0),
        "detected": ("int32", 0, 1),
    }
    _training_set_metadata_fields = {
        "object_id": ("int32", 615, 130779836),
        "ra": ("float32", 0.1757809966802597, 359.82421875),
        "decl": ("float32", -64.76085662841797, 4.181528091430664),
        "gal_l": ("float32", 0.10768099874258041, 359.9438171386719),
        "gal_b": ("float32", -89.61557006835938, 65.93132019042969),
        "ddf": ("int32", 0, 1),
        "hostgal_specz": ("float32", 0.0, 3.4451000690460205),
        "hostgal_photoz": ("float32", 0.0, 2.9993999004364014),
        "hostgal_photoz_err": ("float32", 0.0, 1.7347999811172485),
        "distmod": ("float32", 31.9960994720459, 47.02560043334961),
        "mwebv": ("float32", 0.003000000026077032, 2.746999979019165),
        "target": (
            "categorical",
            6,
            15,
            16,
            42,
            52,
            53,
            62,
            64,
            65,
            67,
            88,
            90,
            92,
            95,
        ),
    }
    _test_set_metadata_fields = {
        "object_id": ("int32", 13, 130788054),
        "ra": ("float32", 0.0, 359.82421875),
        "decl": ("float32", -64.76085662841797, 4.181528091430664),
        "gal_l": ("float32", 0.010369000025093555, 359.99554443359375),
        "gal_b": ("float32", -89.6744155883789, 66.06869506835938),
        "ddf": ("int32", 0, 1),
        "hostgal_specz": ("float32", 0.007699999958276749, 1.2014000415802002),
        "hostgal_photoz": ("float32", 0.0, 3.0),
        "hostgal_photoz_err": ("float32", 0.0, 1.871399998664856),
        "distmod": ("float32", 27.64620018005371, 47.026100158691406),
        "mwebv": ("float32", 0.0020000000949949026, 2.99399995803833),
    }
    _training_set_object_ids = (615, 130779836)
    _training_set_objects_numbers = (47, 352)
    _test_set_object_ids = (13, 130788054)
    _test_set_objects_numbers = (45, 352)

    def generate_check_args(self, **kwargs):
        training_set_records = kwargs.pop("training_set_records", None)
        assert (
            training_set_records is not None
        ), 'Parameter "--training-set-records" is required for plasticc benchmark'
        test_set_records = kwargs.pop("test_set_records", None)
        assert (
            test_set_records is not None
        ), 'Parameter "--training-set-records" is required for plasticc benchmark'
        training_set_metadata_records = kwargs.pop(
            "training_set_metadata_records", None
        )
        assert (
            training_set_metadata_records is not None
        ), 'Parameter "--training-set-metadata-records" is required for plasticc benchmark'
        test_set_metadata_records = kwargs.pop("test_set_metadata_records", None)
        assert (
            test_set_metadata_records is not None
        ), 'Parameter "--test-set-metadata-records" is required for plasticc benchmark'
        print("Generating plasticc")
        self.generate(
            training_set_records,
            test_set_records,
            training_set_metadata_records,
            test_set_metadata_records,
        )

    def generate(
        self,
        training_set_records: int,
        test_set_records: int,
        training_set_metadata_records: int,
        test_set_metadata_records: int,
    ):
        training_set_file = self._output_file_name + "_training_set.csv"
        test_set_file = self._output_file_name + "_test_set.csv"
        training_set_metadata_file = (
            self._output_file_name + "_training_set_metadata.csv"
        )
        test_set_metadata_file = self._output_file_name + "_test_set_metadata.csv"
        if not self._reuse:

            def generate_dataset(
                data_records,
                metadata_records,
                object_numbers,
                data_output,
                metadata_output,
                data_fields,
                metadata_fields,
            ):
                metadata = pd.DataFrame(
                    self._generate_data(metadata_fields, metadata_records)
                )
                numbers = self._split_range_into_random_parts(
                    data_records, metadata_records, object_numbers[0], object_numbers[1]
                )
                data_records = sum(numbers)
                data = pd.DataFrame(self._generate_data(data_fields, data_records))
                ids = np.concatenate([np.repeat(np.array([x]), n) for (x, n) in zip(metadata["object_id"], numbers)])
                data.insert(0, column="object_id", value=ids)

                print("Writing output to", data_output)
                data.to_csv(data_output, index=False)
                print("Writing output to", metadata_output)
                metadata.to_csv(metadata_output, index=False)

            generate_dataset(
                training_set_records,
                training_set_metadata_records,
                self._training_set_objects_numbers,
                training_set_file,
                training_set_metadata_file,
                self._training_set_fields,
                self._training_set_metadata_fields,
            )
            generate_dataset(
                test_set_records,
                test_set_metadata_records,
                self._test_set_objects_numbers,
                test_set_file,
                test_set_metadata_file,
                self._test_set_fields,
                self._test_set_metadata_fields,
            )

        return (
            training_set_file,
            test_set_file,
            training_set_metadata_file,
            test_set_metadata_file,
        )


def main():
    generators = {
        "taxi": TaxiGenerator,
        "census": CensusGenerator,
        "plasticc": PlasticcGenerator,
    }

    parser = argparse.ArgumentParser(description="Generate dataset for a benchmark.")
    parser.add_argument(
        "-m",
        "--mode",
        choices=generators.keys(),
        required=True,
        help="Benchmark to generate dataset for.",
    )
    parser.add_argument(
        "-r",
        "--records",
        required=False,
        type=int,
        help="Number of records to generate. Required for census and taxi.",
    )
    parser.add_argument(
        "-trsr",
        "--training-set-records",
        required=False,
        type=int,
        help="Number of records to generate for training set. Required for plasticc.",
    )
    parser.add_argument(
        "-tesr",
        "--test-set-records",
        required=False,
        type=int,
        help="Number of records to generate for test set. Required for plasticc.",
    )
    parser.add_argument(
        "-trsmr",
        "--training-set-metadata-records",
        required=False,
        type=int,
        help="Number of records to generate for training set metadata. Required for plasticc.",
    )
    parser.add_argument(
        "-tesmr",
        "--test-set-metadata-records",
        required=False,
        type=int,
        help="Number of records to generate for test set metadata. Required for plasticc.",
    )
    parser.add_argument(
        "-o",
        "--output",
        required=True,
        help="File name to write dataset or prefix (in case of plasticc)",
    )
    parser.add_argument(
        "-np",
        "--no-parallel",
        action='store_true',
        help="Disable parallel dataset generation.",
    )
    args = parser.parse_args()
    gen = generators[args.mode](args.output, False, not args.no_parallel, os.cpu_count())
    kwargs = vars(args)
    gen.generate_check_args(**kwargs)


if __name__ == "__main__":
    main()
