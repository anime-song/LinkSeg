import math
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from einops import rearrange
from EMA import EMA

class Res2DMaxPoolModule(nn.Module):
    def __init__(self, input_channels, output_channels, pooling=2):
        super(Res2DMaxPoolModule, self).__init__()
        self.conv_1 = nn.Conv2d(input_channels, output_channels, 3, padding=1)
        self.bn_1 = nn.BatchNorm2d(output_channels)
        self.conv_2 = nn.Conv2d(output_channels, output_channels, 3, padding=1)
        self.bn_2 = nn.BatchNorm2d(output_channels)
        self.relu = nn.ELU()
        self.mp = nn.MaxPool2d(pooling)

        # residual
        self.diff = False
        if input_channels != output_channels:
            self.conv_3 = nn.Conv2d(input_channels, output_channels, 3, padding=1)
            self.bn_3 = nn.BatchNorm2d(output_channels)
            self.diff = True

    def forward(self, x):
        out = self.bn_2(self.conv_2(self.relu(self.bn_1(self.conv_1(x)))))
        if self.diff:
            x = self.bn_3(self.conv_3(x))
        out = x + out
        out = self.mp(self.relu(out))
        return out
    
    
# Transformer modules
"""
    Referenced PyTorch implementation of Vision Transformer by Lucidrains.
    https://github.com/lucidrains/vit-pytorch.git
"""
class Residual(nn.Module):
    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def forward(self, x, **kwargs):
        return self.fn(x, **kwargs) + x


class PreNorm(nn.Module):
    def __init__(self, dim, fn):
        super().__init__()
        self.norm = nn.LayerNorm(dim)
        self.fn = fn

    def forward(self, x, **kwargs):
        return self.fn(self.norm(x), **kwargs)


class FeedForward(nn.Module):
    def __init__(self, dim, hidden_dim, dropout=0.0):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(dim, hidden_dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, dim),
            nn.Dropout(dropout),
        )

    def forward(self, x):
        return self.net(x)


class Attention(nn.Module):
    def __init__(self, dim, heads=8, dim_head=64, dropout=0.0):
        super().__init__()
        inner_dim = dim_head * heads
        self.heads = heads
        self.scale = dim_head ** -0.5

        self.to_qkv = nn.Linear(dim, inner_dim * 3, bias=False)
        self.to_out = nn.Sequential(nn.Linear(inner_dim, dim), nn.Dropout(dropout))

    def forward(self, x, mask=None):
        b, n, _, h = *x.shape, self.heads
        qkv = self.to_qkv(x).chunk(3, dim=-1)
        q, k, v = map(lambda t: rearrange(t, 'b n (h d) -> b h n d', h=h), qkv)

        dots = torch.einsum('bhid,bhjd->bhij', q, k) * self.scale
        mask_value = -torch.finfo(dots.dtype).max

        if mask is not None:
            mask = F.pad(mask.flatten(1), (1, 0), value=True)
            assert mask.shape[-1] == dots.shape[-1], 'mask has incorrect dimensions'
            mask = mask[:, None, :] * mask[:, :, None]
            dots.masked_fill_(~mask, mask_value)
            del mask

        attn = dots.softmax(dim=-1)

        out = torch.einsum('bhij,bhjd->bhid', attn, v)
        out = rearrange(out, 'b h n d -> b n (h d)')
        out = self.to_out(out)
        return out


class Transformer(nn.Module):
    def __init__(self, dim, depth, heads, dim_head, mlp_dim, dropout):
        super().__init__()
        self.layers = nn.ModuleList([])
        for _ in range(depth):
            self.layers.append(
                nn.ModuleList(
                    [
                        Residual(
                            PreNorm(
                                dim, Attention(dim, heads=heads, dim_head=dim_head, dropout=dropout)
                            )
                        ),
                        Residual(PreNorm(dim, FeedForward(dim, mlp_dim, dropout=dropout))),
                    ]
                )
            )

    def forward(self, x, mask=None):
        for attn, ff in self.layers:
            x = attn(x, mask=mask)
            x = ff(x)
        return x




class Conv_2d(nn.Module):
    def __init__(self, input_channels, output_channels, verbose=False, shape=3, padding='same', stride=1, dilation=1, groups=1, dropout=.1, last=False):
        super(Conv_2d, self).__init__()
        self.conv = nn.Conv2d(input_channels, output_channels, shape, stride=stride, padding=padding, dilation=dilation, groups=groups, bias=True)
        self.bn = nn.BatchNorm2d(output_channels, affine=False, track_running_stats=False)
        self.verbose = verbose
        self.relu = nn.ELU()
        self.last = last
        self.conv_1x1 = nn.Conv2d(input_channels, output_channels, 1, stride=stride, padding=padding, dilation=1, groups=groups, bias=True)
        self.bn_1x1 = nn.BatchNorm2d(output_channels, affine=False, track_running_stats=False)
        self.se = EMA(channels=output_channels, factor=4)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x):
        features = x
        x = self.conv(x)
        x = self.se(x)
        x = self.bn(x)
        x = self.bn_1x1(self.conv_1x1(features)) + x
        x = self.relu(x)
        x = self.dropout(x)
        if self.verbose:
            print(x.shape)
        return x



class Conv_net_SSM(nn.Module):
    def __init__(self, input_channels=1, output_channels=16, verbose=False, shape=3, dropout=.2):
        super(Conv_net_SSM, self).__init__()
        self.input_bn = nn.BatchNorm2d(input_channels, affine=False, track_running_stats=False)
    
        self.relu = nn.ELU()
        self.conv1 = Conv_2d(input_channels, output_channels, shape=shape, stride=1, padding='same', verbose=verbose, dilation=1, dropout=dropout)
        self.conv2 = Conv_2d(output_channels, output_channels, shape=shape, stride=1, padding='same', verbose=verbose, dilation=2, dropout=dropout)
        self.conv3 = Conv_2d(output_channels, output_channels, shape=shape, stride=1, padding='same', verbose=verbose, dilation=4, dropout=dropout)
        self.conv4 = Conv_2d(output_channels, output_channels, shape=shape, stride=1, padding='same', verbose=verbose, dilation=8, dropout=dropout)
        self.conv5 = Conv_2d(output_channels, output_channels, shape=shape, stride=1, padding='same', verbose=verbose, dilation=16, dropout=dropout)
        self.conv6 = Conv_2d(output_channels, output_channels, shape=shape, stride=1, padding='same', verbose=verbose, dilation=32, dropout=dropout)
        self.conv7 = Conv_2d(output_channels, output_channels, shape=shape, stride=1, padding='same', verbose=verbose, dilation=64, dropout=dropout)

        

    def forward(self, x):
        if len(x.size()) == 2:
            x = x.unsqueeze(0).unsqueeze(1)
        elif len(x.size()) == 3:
            x = x.unsqueeze(0)
        x = self.input_bn(x)
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        x = self.conv4(x)
        x = self.conv5(x)
        x = self.conv6(x)
        x = self.conv7(x)
        return x


    
class GenericResFrontEnd(nn.Module):
    """
    Evaluation of CNN based Music Tagging.
    Won et al., 2020
    
    Note that, different from the original work, we only stack 3 convolutional layers instead of 7.
    After the convolution layers, we flatten the time-frequency representation to be a vector.
    """

    def __init__(self, conv_ndim=64, nharmonics=1, nmels=64, output_size=32, dropout=0):
        super(GenericResFrontEnd, self).__init__()
        self.input_bn = nn.BatchNorm2d(nharmonics)

        self.layer1 = Res2DMaxPoolModule(nharmonics, conv_ndim, pooling=(2, 2))
        self.layer2 = Res2DMaxPoolModule(conv_ndim, conv_ndim, pooling=(2, 2)) 
        self.layer3 = Res2DMaxPoolModule(conv_ndim, conv_ndim, pooling=(2, 1))
        self.dropout = nn.Dropout(dropout)
        if nmels == 64:
            self.fc = nn.Linear(256, output_size)
        
        

    def forward(self, hcqt):
        # batch normalization
        out = self.input_bn(hcqt)

        # CNN
        out = self.layer1(out)
        out = self.layer2(out)
        out = self.layer3(out)
        
        # permute and channel control
        b, c, f, t = out.shape
        out = out.permute(0, 3, 1, 2)  # batch, time, conv_ndim, freq
        out = out.contiguous().view(b, t, -1)  # batch, time, fc_ndim
        out = self.dropout(out)
        out = self.fc(out)  # batch, time, attention_ndim
        return out
