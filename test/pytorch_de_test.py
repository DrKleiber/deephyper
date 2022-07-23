# -*- coding: utf-8 -*-
"""
Created on Fri Jul 22 15:51:43 2022

@author: Yang
"""

import torch

rv_y = torch.distributions.normal.Normal(loc=0., scale=1.)

y = torch.Tensor([1.0])

test = -rv_y.log_prob(y)