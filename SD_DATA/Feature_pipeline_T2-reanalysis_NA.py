
# general tools
import sys
import subprocess
from glob import glob
from datetime import datetime, timedelta

# data tools
import h5py
import numpy as np

# custom tools
sys.path.insert(0, '/glade/u/home/ksha/WORKSPACE/utils/')
sys.path.insert(0, '/glade/u/home/ksha/WORKSPACE/DL_downscaling/utils/')
sys.path.insert(0, '/glade/u/home/ksha/WORKSPACE/DL_downscaling/')
import data_utils as du
import pipeline_utils as pu
from namelist import * 

# pipeline macros
del_old = True # <--------- !!! delete old batches
aug = False # <------------- !!! aug on-off 

norm = 'norm_std'
seasons = ['djf', 'mam', 'jja', 'son']

vars  = ['TMEAN']
gaps  = [32, 32] # mean grid point distance between croppings (base number, will be randomly moved around)
sizes = [64, 96]
sources = ['ERA', 'JRA']

path = {}
path['JRA'] = JRA_dir
path['ERA'] = ERA_dir

L = {}
L['JRA'] = 365 + 366 + 365 + 365 + 365
L['ERA'] = 365 + 366 + 365 + 365 + 243

# clean-up
if del_old:
    for source in sources:
        cmd = 'rm {}*T{}_*npy'.format(BATCH_dir, source)
        print(cmd)
        subprocess.call(cmd, shell=True)
        
# etopo fields
input_2d = {}
keys_2d = ['etopo_regrid', 'etopo_regrid_clean']

# land mask
with h5py.File(PRISM_dir+'land_mask_NA.hdf', 'r') as hdf_io:
    land_mask = hdf_io['land_mask'][...]

# loop: var --> size --> season --> domain --> aug options
for var in vars:
    for source in sources:
        
        # etopo from an example
        with h5py.File(path[source] + '{}_{}_features_2015_2020.hdf'.format(source, var), 'r') as hdf_io:
            for key in keys_2d:
                input_2d[key] = hdf_io[key][...]
        
        # train date range
        # datetime info
        N_days = L[source] # 2015-2020 (period ending)
        date_list = [datetime(2015, 1, 1, 0) + timedelta(days=x) for x in range(N_days)]
        #
        N_train = 365 + 366 + 365 
        train_list = [datetime(2015, 1, 1, 0) + timedelta(days=x) for x in range(N_train)]
        pick_train = du.dt_match(date_list, train_list)
        IND_train = du.season_ind_sep(train_list, key_format='{}_train') # split by seasons
        #
        N_valid = 365
        valid_list = [datetime(2018, 1, 1, 0) + timedelta(days=x) for x in range(N_valid)]
        pick_valid = du.dt_match(date_list, valid_list)
        IND_valid = du.season_ind_sep(valid_list, key_format='{}_valid') # split by seasons

        print('===== Cropping {} ====='.format(source))
        # import pre-processed features
        # T2 fields
        input_train_3d = {}
        input_valid_3d = {}
        keys_3d = ['{}_REGRID'.format(var)]
        with h5py.File(path[source] + '{}_{}_features_2015_2020.hdf'.format(source, var), 'r') as hdf_io:
            for key in keys_3d:
                input_train_3d[key] = hdf_io[key][pick_train, ...]
                input_valid_3d[key] = hdf_io[key][pick_valid, ...]

        for i, size in enumerate(sizes):
            gap = gaps[i]

            for sea in seasons:
                # output names
                NAME_train = {}; NAME_valid = {}
                # indices
                ind_train = IND_train['{}_train'.format(sea)]
                ind_valid = IND_valid['{}_valid'.format(sea)]

                NAME_train = '{}_BATCH_{}_T{}_{}'.format(var, size, source, sea)
                NAME_valid = '{}_BATCH_{}_V{}_{}'.format(var, size, source, sea)

                # random cropping + batch gen
                print('----- Training data process -----')
                FEATURE_train = pu.random_cropping_regrid(input_train_3d, keys_3d, input_2d, keys_2d, land_mask, size, gap, 
                                                          ind_train, var='TMEAN', rnd_range=4)
                FEATURE_train = pu.feature_norm(FEATURE_train, method=norm, self_norm=True)
                pu.batch_gen(FEATURE_train, batch_size, BATCH_dir, NAME_train, 0);

                print('----- Validation data process -----')
                FEATURE_valid = pu.random_cropping_regrid(input_valid_3d, keys_3d, input_2d, keys_2d, land_mask, size, gap, 
                                                          ind_valid, var='TMEAN', rnd_range=4)
                FEATURE_valid = pu.feature_norm(FEATURE_valid, method=norm, self_norm=True)
                pu.batch_gen(FEATURE_valid, batch_size, BATCH_dir, NAME_valid, 0);
                        

