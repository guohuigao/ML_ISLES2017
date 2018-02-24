import sys
import os

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torch.autograd import Variable

from vox_resnet import VoxResNet_V0, VoxResNet_V1
from refine_net import RefineNet
from dataset import ISLESDataset, BRATSDataset
from evaluator import EvalDiceScore

from FocalLoss import FocalLoss

class CollateFn:
    def __init__(self):
        pass

    def __call__(self, batch_data):
        volume_list = []
        label_list = []
        for volume, label in batch_data:
            volume_list.append(volume)
            label_list.append(label)
        return torch.stack(volume_list), torch.stack(label_list)

def SegLoss(predict, label):
    if False:
        loss = F.binary_cross_entropy_with_logits(predict.squeeze(), label)
    else:
        predict = predict.permute(0, 2, 3, 4, 1).contiguous()
        predict = predict.view(-1, 5)
        label = label.view(-1)
        #loss = FocalLoss(2)(predict, label.long())
        loss = F.cross_entropy(predict, label.long())
    return loss

def SplitAndForward(net, x, split_size=5):
    predict = []
    for i, sub_x in enumerate(torch.split(x, split_size)):
        print(i)
        predict.append(net(sub_x.unsqueeze(0)))
    predict = torch.cat(predict, dim=1)
    return predict

def Evaluate(net, dataset, use_cuda):
    net.eval()
    dataset.eval()
    evaluator_complete = [ EvalDiceScore() ]
    evaluator_core = [ EvalDiceScore() ]
    evaluator_enhancing = [ EvalDiceScore() ]
    for volume, label in dataset:
        volume = Variable(volume)
        if use_cuda:
            volume = volume.cuda()
            label = label.cuda()
        #predict = net(volume.unsqueeze(0))
        predict = SplitAndForward(net, volume)
        predict = torch.max(predict, dim=1)[1] 
        predict = predict.data.long()
        label = label.long()
        predict_core = torch.min(predict > 0, predict != 2)
        label_core = torch.min(label > 0, predict != 2)
        for evaluator in evaluators_core:
            evaluator.AddResult(predict_core, label_core)
        '''
        predict_core = torch.min(predict > 0, predict != 2)
        label_core = torch.min(label > 0, predict != 2)
        for evaluator in evaluators_core:
            evaluator.AddResult(predict_core, label_core)
        predict_core = torch.min(predict > 0, predict != 2)
        label_core = torch.min(label > 0, predict != 2)
        for evaluator in evaluators_core:
            evaluator.AddResult(predict_core, label_core)
        '''
    values = []
    for evaluator in evaluators:
        eval_value = evaluator.Eval()
        print('%s, %f' % (type(evaluator).__name__, eval_value))
        values.append(eval_value)
    return values
    
def Train(train_data, val_data, net, num_epoch, lr, use_cuda=True):
    if use_cuda is not None:
        net.cuda()
    net_ = torch.nn.DataParallel(net, device_ids=[0, 1, 2, 3])
    #net_ = net
    optimizer = torch.optim.Adam(net.parameters(), lr=lr)
    max_fscore = 0
    for i_epoch in range(num_epoch):
        # train
        net_.train()
        train_data.train()
        batch_data = DataLoader(train_data, batch_size=40, shuffle=True, num_workers=12,
                collate_fn=CollateFn(), pin_memory=True)
        train_data.set_trans_prob(i_epoch/3000.0+0.1)
        for i_batch, (volume, target) in enumerate(batch_data):
            volume = Variable(volume)
            target = Variable(target)
            if use_cuda:
                volume = volume.cuda()
                target = target.cuda()
            # forward
            print(volume.size())
            predict = net_(volume)
            loss = SegLoss(predict, target)
            # backward
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        print(('epoch:%d, loss:%f')  % (i_epoch, loss.data[0]))

        # save model for each epoch
        if i_epoch % 200 == 0:
            torch.save(net.state_dict(), ('model/epoch_%d.pt' % i_epoch))
        
        '''
        # test
        if i_epoch % 20 == 0:
            print('val')
            values = Evaluate(net, val_data, use_cuda)
            print('train')
            Evaluate(net, train_data, use_cuda)
            fscore = 2.0*(values[0]*values[1])/(values[0]+values[1]+0.001)
            print('fscore %f' % fscore, max_fscore)
            if fscore > max_fscore:
                max_fscore = fscore
                torch.save(net.state_dict(), 'model/max_fscore.pt')
        '''

def GetDataset():
    data_root = './data/BRATS/train/HGG/'
    folders_HGG = [ os.path.join(data_root, folder) for folder in sorted(os.listdir(data_root)) ] 
    data_root = './data/BRATS/train/LGG/'
    folders_LGG = [ os.path.join(data_root, folder) for folder in sorted(os.listdir(data_root)) ]
    train_folders = folders_HGG[:200] + folders_LGG[:50]
    #train_folders = folders_HGG[:20] + folders_LGG[:20]
    train_dataset = BRATSDataset(train_folders, is_train=True, sample_shape=(128,128,12))
    val_folders = folders_HGG[200:] + folders_LGG[50:]
    #val_folders = folders_HGG[-2:] + folders_LGG[-2:]
    val_dataset = BRATSDataset(val_folders, means=train_dataset.means, 
        norm=train_dataset.norm, is_train=False)
    return train_dataset, val_dataset

if __name__ == '__main__':
    train_dataset, val_dataset = GetDataset()
    #net = VoxResNet_V0(7, 2)
    net = RefineNet(4,5)
    #net = VoxResNet_V1(9, 2)

    Train(train_dataset, val_dataset, net,
        num_epoch=6000, lr=0.0001, use_cuda=True)