#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Copyright 2018 hcab14@gmail.com.
#
# This is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
#
# This software is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this software; see the file COPYING.  If not, write to
# the Free Software Foundation, Inc., 51 Franklin Street,
# Boston, MA 02110-1301, USA.
#

import numpy as np
from gnuradio import blocks
from gnuradio import gr
import pmt

class find_offsets(gr.sync_block):
    """
    This block determines offsets (number of samples) between a number of streams using rx_time tags
    Note that it assumes that the offsets are close to integer multiples of samples
    """
    def __init__(self, num_streams, same_kiwi):
        gr.sync_block.__init__(
            self,
            name    = 'find_offsets',
            in_sig  = num_streams*(np.complex64,),
            out_sig = num_streams*(np.complex64,))
        self._num_streams = num_streams
        self._same_kiwi   = same_kiwi
        self._fs          = np.zeros(num_streams, dtype=np.float64) ## is set from 'rx_rate' tags
        self._tags        = [[] for _ in range(num_streams)]
        self._delays      = np.zeros(num_streams, dtype=np.int)
        self._port_fs     = pmt.intern('fs')
        self.message_port_register_out(self._port_fs)
        self.set_tag_propagation_policy(gr.TPP_ONE_TO_ONE)

    def start(self):
        self._delays[:] = 0
        self._fs[:]     = 0
        self._tags      = [[] for _ in range(self._num_streams)]
        self._offsets   = None
        return True

    def work(self, input_items, output_items):
        n = len(input_items[0])

        all_tags = [self.get_tags_in_window(i, 0, n) for i in range(self._num_streams)]

        ## (1) 'rx_rate' tags
        tags_ok = [False]*self._num_streams
        for i in range(self._num_streams):
            tags = [t for t in all_tags[i] if pmt.to_python(t.key) == 'rx_rate']
            if len(tags) != 0:
                self._fs[i] = np.mean(np.array([pmt.to_python(t.value) for t in tags], dtype=np.float64))
                tags_ok[i] = True
        if all(tags_ok):
            msg_out = pmt.make_dict()
            msg_out = pmt.dict_add(msg_out, pmt.intern('fs'), pmt.to_pmt(self._fs))
            self.message_port_pub(self._port_fs, msg_out)

        ## (2) 'rx_time' tags
        tags_ok = [False]*self._num_streams
        f       = lambda x : np.float64(x[0])+x[1]
        for i in range(self._num_streams):
            tags = [t for t in all_tags[i] if pmt.to_python(t.key) == 'rx_time']
            for tag in tags:
                self._tags[i].extend([{'samp_num' : tag.offset,
                                       'gnss_sec' : f(pmt.to_python(tag.value))}])
            #gr.log.info('tags[{}] = {}'.format(i,self._tags[i][0]))
            tags_ok[i] = (self._tags[i] != [])

        ## (3) compute delays for aligning the streams
        while all(tags_ok):
            ## compute differences w.r.t 1st in number of samples
            fd = lambda x,y,fsx,fsy: x['gnss_sec']-y['gnss_sec'] - (x['samp_num']/fsx-y['samp_num']/fsy)
            ds = np.array([fd(self._tags[i][0], self._tags[0][0], self._fs[i], self._fs[0]) + self._delays[i]/self._fs[i]-self._delays[0]/self._fs[0]
                           for i in range(1,self._num_streams)], dtype=np.double) * self._fs[1:]
            print('ds=', ds)
            ## remove the processed tags and update tags_ok
            for i in range(self._num_streams):
                self._tags[i] =  self._tags[i][1:]
                tags_ok[i]    = (self._tags[i] != [])
            ## compute offsets
            offsets = np.zeros(self._num_streams, dtype=np.int)
            offsets[0]  = 0
            offsets[1:] = np.round(ds)
            print('offsets=', offsets)
            if np.max(np.abs(offsets)) < 5*self._fs[0] and (np.max(np.abs(offsets[1:]-ds)) < 0.2 or not self._same_kiwi):
                offsets -= np.max(offsets)
                print('offsets=', offsets)
                if np.max(-offsets) > 0:
                    for i in range(self._num_streams):
                        to_consume = min(-offsets[i], len(input_items[i]))
                        gr.log.info('to_consume[{}] = {}'.format(i, to_consume))
                        self.consume(i, to_consume)
                        self._delays[i] += to_consume
                        self._tags[i] = []
                    return 0
            else:
                gr.log.warn('ds={} fs={}'.format(ds, self._fs))

        ## (4) pass through all data
        for i in range(self._num_streams):
            output_items[i][:] = input_items[i]

        return n

class align_streams(gr.hier_block2):
    """
    Block for aligning KiwiSDR IQ streams with GNSS timestamps
    Note that all used IQ streams have to be from the same KiwiSDR
    """
    def __init__(self, num_streams, same_kiwi=True):
        gr.hier_block2.__init__(self,
                                "align_streams",
                                gr.io_signature(num_streams, num_streams, gr.sizeof_gr_complex),
                                gr.io_signature(num_streams, num_streams, gr.sizeof_gr_complex))
        self._find_offsets = find_offsets(num_streams, same_kiwi)
        for i in range(num_streams):
            self.connect((self, i),
                         (self._find_offsets, i),
                         (self, i))

    def get_find_offsets(self):
        return self._find_offsets
