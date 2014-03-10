#!/usr/bin/env python

'''
todo

 * testing
     * check why top couple bins don't match in plot
     * check some unnormalized count rows
     * check some normalized count rows
     * check some summary table rows
'''

import bamliquidator_internal.normalize_plot_and_summarize as npt 
from bamliquidator_internal.chromosome_list import chromosomes

import argparse
import os
import subprocess
import tables
import datetime
import csv
from time import time 

# creates empty file, overwriting any prior existing files
def create_count_table(h5file):
    class BinCount(tables.IsDescription):
        bin_number = tables.UInt32Col(    pos=0)
        cell_type  = tables.StringCol(16, pos=1)
        chromosome = tables.StringCol(16, pos=2)
        count      = tables.UInt64Col(    pos=3)
        # todo: rename file_name to genome or line
        file_name  = tables.StringCol(64, pos=4)

    table = h5file.create_table("/", "bin_counts", BinCount, "bin counts")

    table.flush()

    return table

def create_regions_table(h5file):
    class Region(tables.IsDescription):
        file_name        = tables.StringCol(64, pos=0)
        chromosome       = tables.StringCol(16, pos=1)
        region_name      = tables.StringCol(64, pos=2)
        start            = tables.UInt64Col(    pos=3)
        stop             = tables.UInt64Col(    pos=4)
        strand           = tables.StringCol(1,  pos=5)
        count            = tables.UInt64Col(    pos=6)
        normalized_count = tables.Float64Col(   pos=7)

    table = h5file.create_table("/", "region_counts", Region, "region counts")

    table.flush()

    return table

def all_bam_file_paths_in_directory(bam_directory):
    bam_file_paths = []
    for dirpath, _, files in os.walk(bam_directory, followlinks=True):
        for file_ in files:
            if file_.endswith(".bam"):
                bam_file_paths.append(os.path.join(dirpath, file_))
    return bam_file_paths

def bam_file_paths_with_no_counts(counts, bam_file_paths):
    with_no_counts = []

    for bam_file_path in bam_file_paths:
        count_found = False
        condition = "file_name == '%s'" % os.path.basename(bam_file_path)

        for _ in counts.where(condition):
            count_found = True

        if not count_found:
            with_no_counts.append(bam_file_path)

    return with_no_counts

# returns a tuple of two dictionaries:
# 1) (bam_file_name, chromosome) -> sequence length
# 2) base_bam_file_name -> total mapped count
def lengths_and_total_counts(bam_file_paths):
    file_chromosome_tuple_to_length = {}
    file_to_count = {}

    chr_col         = 0
    length_col      = 1
    mapped_read_col = 2

    for bam_file_path in bam_file_paths:
        args = ["samtools", "idxstats", bam_file_path]
        output = subprocess.check_output(args)
        # skip last two lines: the unmapped chromosome line and the empty line
        reader = csv.reader(output.split('\n')[:-2], delimiter='\t')
        file_name = os.path.basename(bam_file_path)
        file_count = 0
        for row in reader:
            file_count += int(row[mapped_read_col])
            file_chromosome_tuple_to_length[file_name, row[chr_col]] = int(row[length_col])
        file_to_count[file_name] = file_count

    return file_chromosome_tuple_to_length, file_to_count

def liquidate(bam_file_paths, output_directory, file_chromosome_tuple_to_length, file_to_count, 
              counts_file_path, executable_path, bin_size = None, region_file = None):

    if (bin_size is not None and region_file is not None) or (bin_size is None and region_file is None):
        sys.exit("either bin_size or region_file must be provided, but not both")

    for i, bam_file_path in enumerate(bam_file_paths):
        print "Liquidating %s (file %d of %d, %s)" % (
            bam_file_path, i+1, len(bam_file_paths), datetime.datetime.now().strftime('%H:%M:%S'))
        cell_type = os.path.basename(os.path.dirname(bam_file_path))

        bam_file_name = os.path.basename(bam_file_path)

        if bin_size:
            args = [executable_path, cell_type, str(bin_size), bam_file_path, counts_file_path]

            for chromosome in chromosomes:
                args.append(chromosome)
                args.append(str(file_chromosome_tuple_to_length[bam_file_name, chromosome]))
        else:
            args = [executable_path, region_file, bam_file_path, counts_file_path]

        start = time()
        return_code = subprocess.call(args)
        end = time()
        duration = end - start
        if bin_size:
            reads = file_to_count[bam_file_name]
            rate = reads / (10**6) / duration
            print "Liquidation completed: %f seconds, %d reads, %f millions of reads per second" % (duration, reads, rate)
        else:
            print "Liquidation completed: %f seconds" % (duration)

        if return_code != 0:
            print "%s failed with exit code %d" % (executable_path, return_code)
            exit(return_code)

    if bin_size:
        counts_file = tables.open_file(counts_file_path, mode = "r")
        npt.normalize_plot_and_summarize(counts_file.root.bin_counts, output_directory, bin_size, file_to_count) 
    else:
        counts_file = tables.open_file(counts_file_path, mode = "r+")
        npt.normalize(counts_file.root.region_counts, file_to_count) 
    counts_file.close()

def main():
    parser = argparse.ArgumentParser(description='Count the number of base pair reads in each bin or region '
                                                 'in the bam file(s) at the given directory, and then normalize, plot bins, '
                                                 'and summarize the counts in the output directory.  For additional '
                                                 'help, please see https://github.com/BradnerLab/pipeline/wiki')
    parser.add_argument('-o', '--output_directory', default='output',
                        help='Directory to create and output the h5 and/or html files to (aborts if already exists). '
                             'Default is "./output".')
    parser.add_argument('-c', '--counts_file',
                        help='HDF5 counts file from a prior run to be appended to.  If unspecified, defaults to '
                             'creating a new file "counts.h5" in the output directory.')
    parser.add_argument('-b', '--bin_size', type=int, default=100000,
                        help="Number of base pairs in each bin -- the smaller the bin size the longer the runtime and "
                             "the larger the data files (default is 100000). This argument is ignored if regions are provided.")
    parser.add_argument('-r', '--regions_file',
                        help='a region file in either .gff or .bed format')
    parser.add_argument('bam_file_path', 
                        help='The directory to recursively search for .bam files for counting.  Every .bam file must '
                             'have a corresponding .bai file at the same location.  To count just a single file, '
                             'provide the .bam file path instead of a directory.  The parent directory of each .bam '
                             'file is interpreted as the cell type (e.g. mm1s might be an appropriate directory '
                             'name).  Bam files in the same directory are grouped together for plotting. Plots use '
                             'normalized counts, such that all .bam files in the same directory have bin '
                             'counts that add up to 1 for each chromosome.  If your .bam files are not in this '
                             'directory format, please consider creating a directory of sym links to your actual '
                             '.bam and .bai files. If the .bam file already has 1 or more reads in the HDF5 counts file, '
                             'then that .bam file is skipped from liquidation, but is still included in normalization, '
                             'plotting, and summaries.')
    args = parser.parse_args()

    assert(tables.__version__ >= '3.0.0')

    if args.regions_file is None:
        region_mode = False 
    else:
        region_mode = True
        args.bin_size = None

    executable_path = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                                   "bamliquidator_internal",
                                   "bamliquidator_regions" if region_mode else "bamliquidator_bins")
    if not os.path.isfile(executable_path):
        sys.exit("%s is missing -- try cd'ing into the directory and running 'make'" % executable_path)

    os.mkdir(args.output_directory)

    if args.counts_file is None:
        args.counts_file = os.path.join(args.output_directory, "counts.h5")
    
        counts_file = tables.open_file(args.counts_file, mode = "w",
                                       title = "bam liquidator genome bin read counts")
    else:
        counts_file = tables.open_file(args.counts_file, "r+")

    try: 
        counts = counts_file.root.region_counts if region_mode else counts_file.root.bin_counts
    except:
        counts = create_regions_table(counts_file) if region_mode else create_count_table(counts_file)

    if os.path.isdir(args.bam_file_path):
        bam_file_paths = all_bam_file_paths_in_directory(args.bam_file_path)
    else:
        bam_file_paths = [args.bam_file_path]
   
    bam_file_paths = bam_file_paths_with_no_counts(counts, bam_file_paths)

    counts_file.close() # bamliquidator_bins/bamliquidator_regions will open this file and 
                        # modify it, so it is best that we not hold an out of sync reference

    file_chromosome_tuple_to_length, file_to_count = lengths_and_total_counts(bam_file_paths)

    liquidate(bam_file_paths, args.output_directory, file_chromosome_tuple_to_length, file_to_count, 
              args.counts_file, executable_path, args.bin_size, args.regions_file)

if __name__ == "__main__":
    main()

'''
   The MIT License (MIT) 

   Copyright (c) 2013 John DiMatteo (jdimatteo@gmail.com)

   Permission is hereby granted, free of charge, to any person obtaining a copy
   of this software and associated documentation files (the "Software"), to deal
   in the Software without restriction, including without limitation the rights
   to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
   copies of the Software, and to permit persons to whom the Software is
   furnished to do so, subject to the following conditions:

   The above copyright notice and this permission notice shall be included in
   all copies or substantial portions of the Software.

   THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
   IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
   FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
   AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
   LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
   OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
   THE SOFTWARE. 
'''
