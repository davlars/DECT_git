#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Oct 19 12:17:37 2016

@author: davlars
"""

from __future__ import division, print_function, unicode_literals, absolute_import
from future import standard_library
standard_library.install_aliases()
from builtins import super

from gpumci import ProjectionGeometry3D, CudaProjectorOptimized, CudaProjectorSpectrum, util
from math import pi, sin, cos
import numpy as np
from scipy.io import loadmat
import odl
#import matplotlib.pyplot as plt
import pickle
import pandas as pd
import nibabel as nib
import time


def get_spectrum(n):
    # Get spectrum with n points
    path = '/home/davlars/AD_GPUMCI/data/'
    dat = np.loadtxt(path+'spectrumLowVoltage.txt')
    energies = dat[:, 0] / 1000.0
    spectrum = dat[:, 1]
    
    indices = np.linspace(0, energies.size - 1, n, dtype=int)
    energies = energies[indices]
    spectrum = spectrum[indices]
    spectrum /= spectrum.sum()
    return energies, spectrum


def getPhantom(phantomName):
    #nifit data 
    path = '/home/davlars/AD_GPUMCI/phantoms/'
    nii = nib.load(path+phantomName)
    phantomdata = nii.get_data()
                          
    densities = np.zeros_like(phantomdata, dtype=float)
    densities[phantomdata == 0] = 1.0   
    densities[phantomdata == 1] = 1.0       
    densities = np.reshape(densities, np.shape(phantomdata), order='F')
    #densities = densities[:,:,::-1]

    # Mat material indices, happen to be very similiar.
    mat = np.zeros(densities.shape, dtype=int, order='F')
    mat[phantomdata == 0] = 0     # air
    mat[phantomdata == 1] = 1     # water
    
    #size
    phantomSize = np.shape(phantomdata)
    
    return densities, mat, phantomSize
    
def make_gain(sourceAxisDistance,
              detectorAxisDistance,
              detectorOrigin,
              nPixels,
              pixelSize):
    # Create gain that gives constant image result.
    x, y = np.meshgrid(np.linspace(detectorOrigin[1], detectorOrigin[1] + nPixels[1] * pixelSize[1], nPixels[1]),
                       np.linspace(detectorOrigin[0], detectorOrigin[0] + nPixels[0] * pixelSize[0], nPixels[0]))
    r = (detectorAxisDistance+ sourceAxisDistance)
    dist2 = (x**2 + y**2 + r**2) / r**2
    return 1.0 / np.sqrt(dist2)

def calculate_projections(phantomName, saveName, turnNumber):
    # Set geometry parameters
    phantomPixelSize = 230./512
    volumeSize = np.array([300, 300, 300])*phantomPixelSize
    volumeOrigin = -volumeSize/2
    volumeOrigin[-1] = 0.0    

    # Continuous volume
    volume = odl.IntervalProd(volumeOrigin, volumeOrigin+volumeSize)

    pixelSize = np.array([2.4, 2.4])   
    sourceAxisDistance = 542.8
    detectorAxisDistance = 542.8
    pitch_mm = 0
    n_turns = 1 
    
    #Load phantom
    den, mat, phantomSize = getPhantom(phantomName)
    
    # Discretization parameters
    nVoxels, nPixels = np.array(phantomSize), [250, 10]
    nProjection = 360 * n_turns
    
    # Scale factors
    detectorSize = pixelSize * nPixels
    
    detectorOrigin = -detectorSize/2

    
    #Define projection geometry
    # Make a helical cone beam geometry with flat detector
    # Angles: uniformly spaced, n = nProjection, min = 0, max = n_turns * 2 * pi
    angle_partition = odl.uniform_partition(2 * np.pi* turnNumber, 
                                            2 * np.pi* (turnNumber + n_turns), 
                                            nProjection)
    
    # Detector: uniformly sampled, n = nPixels, min = detectorOrigin, max = detectorOrigin+detectorSize
    detector_partition = odl.uniform_partition(detectorOrigin, detectorOrigin+detectorSize, nPixels)
    
    # Spiral has a pitch of pitch_mm, we run n_turns rounds (due to max angle = 8 * 2 * pi)
    geometry = odl.tomo.HelicalConeFlatGeometry(angle_partition, 
                                                detector_partition, 
                                                src_radius=sourceAxisDistance, 
                                                det_radius=detectorAxisDistance,
                                                pitch=pitch_mm,
                                                pitch_offset=6)
    
    energies, spectrum = get_spectrum(20)
    photons_per_pixel = 2000
    spectrum *= photons_per_pixel
        
    gain = make_gain(sourceAxisDistance,
                     detectorAxisDistance,
                     detectorOrigin,
                     nPixels,
                     pixelSize)
    
    spectrum = np.tile(spectrum[:, None, None], [1, nPixels[0], nPixels[1]])
    
    materials = ['carbon', 'potassium']
    
    projector = CudaProjectorSpectrum(volume, nVoxels, geometry,
                                      energies, energies, spectrum, materials)
    
    #projector = odl.tomo.RayTransform
    
    el = projector.domain.element([den, mat])
    #el.show(title='phantom')
    
    with odl.util.Timer():
        result = projector(el)
    
    #    outFilename = '70100644Phantom'
    projections = np.empty([nProjection, nPixels[0], nPixels[1]])
    for i, proj in enumerate(result):
        primary = proj[0]
        secondary = proj[1]
        projections[i, ...] = np.asarray(primary + secondary)
        
    np.save(saveName + '.npy', projections)
    pickle.dump(geometry, open(saveName + '_geometry.p', 'wb+'))
    

if __name__ == '__main__':
    
    names = ['K_in_C_2.nii',
             '70100644Phantom_labelled_no_bed.nii',
             '70100644Phantom_labelled.nii',
             '70114044Phantom_labelled.nii',
             '70122044Phantom_labelled.nii',
             '70135144Phantom_labelled.nii',
             '70141544Phantom_labelled.nii',
             '70153744Phantom_labelled.nii',
             '70162244Phantom_labelled.nii',
             '70171844Phantom_labelled.nii',
             '70182144Phantom_labelled.nii',
             '70193044Phantom_labelled.nii']
            
    folder = '/home/davlars/' #/media/davlars/2CCCC8C14D95A6C1/CTsimulations/140kV'
             
    # GPU PARALLELLIZATION
    # give input in bash as:
    # 
    # CUDA_VISIBLE_DEVICES=0 python HelicalCT_Head_Geriatrics.py NumGPUS GPUIndex_0 & CUDA_VISIBLE_DEVICES=1 python... && fg
    # 
    # which for 2 GPUs mean:
    # CUDA_VISIBLE_DEVICES=0 python HelicalCT_Head_Geriatrics.py 2 0 & CUDA_VISIBLE_DEVICES=1 python HelicalCT_Head_Geriatrics.py 2 1 && fg
    
    import sys
    narg = len(sys.argv)
    if narg == 1:
        ngpus = 1
        gpuindex = 0
    elif narg == 3:
        ngpus = int(sys.argv[1])
        gpuindex = int(sys.argv[2])
    else:
        assert False
        
    print('running with {} GPUs, index {}'.format(ngpus, gpuindex))
        
    nsimul = 60
    
    for phantomName in names[:1]:
        for number_of_simu in range(1):
            for turnNumber in range(16,17):
                print('calculating phantom: {}'.format(phantomName) + 
                      ', sim. no: {}'.format(number_of_simu) + 
                      ', turn no: {}'.format(turnNumber))
                saveName = ('{}/K_in_C_80kV'.format(folder))
                calculate_projections(phantomName, saveName, turnNumber)

