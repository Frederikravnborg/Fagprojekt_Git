#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@Author: An Tao
@Contact: ta19@mails.tsinghua.edu.cn
@File: model.py
@Time: 2020/3/23 5:39 PM
"""
#%%
import torch
import torch.nn as nn
import torch.nn.init as init
import torch.nn.functional as F
import numpy as np
import itertools
import matplotlib.pyplot as plt
from LoadData_Torch import data_split

#from loss import ChamferLoss, CrossEntropyLoss

#%%
# Define a function that visualizes a point cloud
def visualize_point_cloud(pcd):
    # Convert it to a numpy array
    #points = np.asarray(pcd)
    points = pcd.detach().numpy()

    # Plot it using matplotlib with tiny points and constrained axes
    fig = plt.figure()
    ax = fig.add_subplot(projection='3d')
    ax.scatter(points[:,0], points[:,1], points[:,2], s=0.1, cmap='hsv')
    ax.set_xlabel('X')
    ax.set_ylabel('Y')
    ax.set_zlabel('Z')
    ax.set_box_aspect((1,1,1)) # Constrain the axes
    #ax.set_proj_type('ortho') # Use orthographic projection
    #ax.set_xlim(-1,1) # Set x-axis range
    #ax.set_ylim(-1,1) # Set y-axis range
    #ax.set_zlim(-1,1) # Set z-axis range
    plt.show()

def knn(x, k):
    batch_size = x.size(0)
    num_points = x.size(2)

    inner = -2*torch.matmul(x.transpose(2, 1), x)
    xx = torch.sum(x**2, dim=1, keepdim=True)
    pairwise_distance = -xx - inner - xx.transpose(2, 1)
 
    idx = pairwise_distance.topk(k=16, dim=-1)[1]            # (batch_size, num_points, k)

    if idx.get_device() == -1:
        idx_base = torch.arange(0, batch_size).view(-1, 1, 1)*num_points
    else:
        idx_base = torch.arange(0, batch_size, device=idx.get_device()).view(-1, 1, 1)*num_points
    idx = idx + idx_base
    idx = idx.view(-1)

    return idx


def local_cov(pts, idx):
    batch_size = pts.size(0)
    num_points = pts.size(2)
    pts = pts.view(batch_size, -1, num_points)              # (batch_size, 3, num_points)
 
    _, num_dims, _ = pts.size()

    x = pts.transpose(2, 1).contiguous()                    # (batch_size, num_points, 3)
    x = x.view(batch_size*num_points, -1)[idx, :]           # (batch_size*num_points*2, 3)
    x = x.view(batch_size, num_points, -1, num_dims)        # (batch_size, num_points, k, 3)

    x = torch.matmul(x[:,:,0].unsqueeze(3), x[:,:,1].unsqueeze(2))  # (batch_size, num_points, 3, 1) * (batch_size, num_points, 1, 3) -> (batch_size, num_points, 3, 3)
    # x = torch.matmul(x[:,:,1:].transpose(3, 2), x[:,:,1:])
    x = x.view(batch_size, num_points, 9).transpose(2, 1)   # (batch_size, 9, num_points)

    x = torch.cat((pts, x), dim=1)                          # (batch_size, 12, num_points)

    return x


def local_maxpool(x, idx):
    batch_size = x.size(0)
    num_points = x.size(2)
    x = x.view(batch_size, -1, num_points)
 
    _, num_dims, _ = x.size()

    x = x.transpose(2, 1).contiguous()                      # (batch_size, num_points, num_dims)
    x = x.view(batch_size*num_points, -1)[idx, :]           # (batch_size*n, num_dims) -> (batch_size*n*k, num_dims)
    x = x.view(batch_size, num_points, -1, num_dims)        # (batch_size, num_points, k, num_dims)
    x, _ = torch.max(x, dim=2)                              # (batch_size, num_points, num_dims)

    return x


def get_graph_feature(x, k=20, idx=None):
    batch_size = x.size(0)
    num_points = x.size(2)
    x = x.view(batch_size, -1, num_points)      # (batch_size, num_dims, num_points)
    if idx is None:
        idx = knn(x, k=k)                       # (batch_size, num_points, k)
 
    _, num_dims, _ = x.size()

    x = x.transpose(2, 1).contiguous()          # (batch_size, num_points, num_dims)
    feature = x.view(batch_size*num_points, -1)[idx, :]                 # (batch_size*n, num_dims) -> (batch_size*n*k, num_dims)
    feature = feature.view(batch_size, num_points, k, num_dims)         # (batch_size, num_points, k, num_dims)
    x = x.view(batch_size, num_points, 1, num_dims).repeat(1, 1, k, 1)  # (batch_size, num_points, k, num_dims)
    
    feature = torch.cat((feature-x, x), dim=3).permute(0, 3, 1, 2)      # (batch_size, num_points, k, 2*num_dims) -> (batch_size, 2*num_dims, num_points, k)
  
    return feature                              # (batch_size, 2*num_dims, num_points, k)

class FoldNet_Encoder(nn.Module):
    def __init__(self):
        super(FoldNet_Encoder, self).__init__()
        self.k = 32
        self.n = 2048   # input point cloud size
        self.mlp1 = nn.Sequential(
            nn.Conv1d(12, 64, 1),
            nn.ReLU(),
            nn.Conv1d(64, 64, 1),
            nn.ReLU(),
            nn.Conv1d(64, 64, 1),
            nn.ReLU(),
        )
        self.linear1 = nn.Linear(64, 64)
        self.conv1 = nn.Conv1d(64, 128, 1)
        self.linear2 = nn.Linear(128, 128)
        self.conv2 = nn.Conv1d(128, 1024, 1)
        self.mlp2 = nn.Sequential(
            nn.Conv1d(1024, 512, 1),
            nn.ReLU(),
            nn.Conv1d(512, 512, 1),
        )

    def graph_layer(self, x, idx):           
        x = local_maxpool(x, idx)    
        x = self.linear1(x)  
        x = x.transpose(2, 1)                                     
        x = F.relu(self.conv1(x))                            
        x = local_maxpool(x, idx)  
        x = self.linear2(x) 
        x = x.transpose(2, 1)                                   
        x = self.conv2(x)                       
        return x

    def forward(self, pts):
        pts = pts.transpose(2, 1)               # (batch_size, 3, num_points)
        idx = knn(pts, k=self.k)
        x = local_cov(pts, idx)                 # (batch_size, 3, num_points) -> (batch_size, 12, num_points])            
        x = self.mlp1(x)                        # (batch_size, 12, num_points) -> (batch_size, 64, num_points])
        x = self.graph_layer(x, idx)            # (batch_size, 64, num_points) -> (batch_size, 1024, num_points)
        x = torch.max(x, 2, keepdim=True)[0]    # (batch_size, 1024, num_points) -> (batch_size, 1024, 1)
        x = self.mlp2(x)                        # (batch_size, 1024, 1) -> (batch_size, feat_dims, 1)
        feat = x.transpose(2,1)                 # (batch_size, feat_dims, 1) -> (batch_size, 1, feat_dims)
        return feat                             # (batch_size, 1, feat_dims)


class FoldNet_Decoder(nn.Module):
    def __init__(self):
        super(FoldNet_Decoder, self).__init__()
        self.m = 2025  # 45 * 45.
        self.shape = 'sphere'
        self.meshgrid = [[-0.3, 0.3, 45], [-0.3, 0.3, 45]]
        self.sphere = np.load("sphere.npy")
        self.gaussian = np.load("gaussian.npy")
        if self.shape == 'plane':
            self.folding1 = nn.Sequential(
                nn.Conv1d(512+2, 512, 1),
                nn.ReLU(),
                nn.Conv1d(512, 512, 1),
                nn.ReLU(),
                nn.Conv1d(512, 3, 1),
            )
        else:
            self.folding1 = nn.Sequential(
                nn.Conv1d(512+3, 512, 1),
                nn.ReLU(),
                nn.Conv1d(512, 512, 1),
                nn.ReLU(),
                nn.Conv1d(512, 3, 1),
            )  
        self.folding2 = nn.Sequential(
            nn.Conv1d(512+3, 512, 1),
            nn.ReLU(),
            nn.Conv1d(512, 512, 1),
            nn.ReLU(),
            nn.Conv1d(512, 3, 1),
        )

    def build_grid(self, batch_size):
        if self.shape == 'plane':
            x = np.linspace(*self.meshgrid[0])
            y = np.linspace(*self.meshgrid[1])
            points = np.array(list(itertools.product(x, y)))
        elif self.shape == 'sphere':
            points = self.sphere
        elif self.shape == 'gaussian':
            points = self.gaussian
        points = np.repeat(points[np.newaxis, ...], repeats=batch_size, axis=0)
        points = torch.tensor(points)
        return points.float()

    def forward(self, x):
        x = x.transpose(1, 2).repeat(1, 1, self.m)      # (batch_size, feat_dims, num_points)
        points = self.build_grid(x.shape[0]).transpose(1, 2)  # (batch_size, 2, num_points) or (batch_size, 3, num_points)
        if x.get_device() != -1:
            points = points.cuda(x.get_device())
        cat1 = torch.cat((x, points), dim=1)            # (batch_size, feat_dims+2, num_points) or (batch_size, feat_dims+3, num_points)
        folding_result1 = self.folding1(cat1)           # (batch_size, 3, num_points)
        cat2 = torch.cat((x, folding_result1), dim=1)   # (batch_size, 515, num_points)
        folding_result2 = self.folding2(cat2)           # (batch_size, 3, num_points)
        return folding_result2.transpose(1, 2)          # (batch_size, num_points ,3)
    
class ReconstructionNet(nn.Module):
    def __init__(self):
        super(ReconstructionNet, self).__init__()
        self.encoder = FoldNet_Encoder()
        self.decoder = FoldNet_Decoder()

    def forward(self, input):
        feature = self.encoder(input)
        output = self.decoder(feature)
        return output, feature

    def get_parameter(self):
        return list(self.encoder.parameters()) + list(self.decoder.parameters())

#%%

if __name__ == '__main__':
    n: int = 1024*2

    female_data_loader_train, _, _, _ = data_split(n_points=n)
    for batch in female_data_loader_train:
        #visualize_point_cloud(batch[0, :, :])
        pcs = batch
        #print(pcs.size())

        fNet = ReconstructionNet()
        feature, parameters = fNet.forward(pcs)
        break


# %%
visualize_point_cloud(batch[0, :, :])
visualize_point_cloud(feature[0, :, :])


# %%
