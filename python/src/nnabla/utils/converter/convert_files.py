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

import os
import sys

from nnabla.utils.converter.nnabla import NnpReader
from nnabla.utils.converter.nnabla import NnpExporter
from nnabla.utils.converter.nnablart import NnbExporter
from nnabla.utils.converter.nnablart import CsrcExporter


def convert_files(args, ifiles, output):
    nnp = None
    if args.read_format == 'NNP':
        # Input file that has unsuported extension store into output nnp archive or directory.
        nnp = NnpReader(*ifiles, expand_network=args.nnp_expand_network).read()

    if nnp is not None:
        output_ext = os.path.splitext(output)[1].lower()
        if (os.path.isdir(output) and args.export_format == 'NNP') or output_ext == '.nnp':
            parameter_type = 'protobuf'
            if args.nnp_parameter_nntxt:
                parameter_type = 'included'
            elif args.nnp_parameter_h5:
                parameter_type = 'h5'
            if args.nnp_exclude_parameter:
                parameter_type = 'none'
            NnpExporter(nnp, parameter_type).export(output)

        elif output_ext == '.nnb':
            NnbExporter(nnp).export(output)

        elif os.path.isdir(output) and args.export_format == 'CSRC':
            CsrcExporter(nnp).export(output)

        else:
            print('Output file ({}) is not supported or output directory does not exist.'.format(
                output_ext))
            return False
    else:
        print('Read from [{}] failed.'.format(ifiles))
    return False