import torchvision
import torchvision.transforms as transforms
import torch
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import matplotlib.cm as cm
import numpy as np
import json
import math
import re
from torch import nn, optim
import torch.nn.functional as F
from torch.autograd import Variable
from torch.utils.data.sampler import SubsetRandomSampler
import warnings
from sklearn import svm
from keras.datasets import fashion_mnist
import pandas as pd
import joblib
import os
import random
import time
import multiprocessing
from multiprocessing import Process, Pool, Queue, Lock, Value
import itertools
import pickle
import importlib
from PIL import Image
import h5py
import argparse
import yaml
import imat_dataset
import visualize

# imports for segmentation
from torch.utils.data import DataLoader
from torch.utils.data import Dataset as BaseDataset

# imports external packages (from folder)
import helpers
import utils
import transforms as T
from torchvision.models.detection.faster_rcnn import FastRCNNPredictor
from torchvision.models.detection.mask_rcnn import MaskRCNNPredictor, MaskRCNN
from torchvision.models.detection.rpn import AnchorGenerator
from torchvision.ops import MultiScaleRoIAlign
from torchvision.ops import misc as misc_nn_ops
import pycocotools
import coco_utils, coco_eval, engine, utils
from timm.models.layers import get_act_layer
from timm import create_model
import effdet
from effdet import BiFpn, DetBenchTrain, EfficientDet, load_pretrained, load_pretrained, HeadNet
import subprocess

is_colab = False
try:
    from google.colab import drive
    print("Running on Google Colab")
    is_colab = True
except:
    print("Running on non Google Colab env")

warnings.filterwarnings('ignore')
pd.set_option('display.max_rows', 500)
pd.set_option('display.max_columns', 500)
pd.set_option('display.width', 150)

# seeds
# following is needed for reproducibility
# refer to https://pytorch.org/docs/stable/notes/randomness.html
torch.backends.cudnn.deterministic = True
torch.backends.cudnn.benchmark = False
seed = 1
np.random.seed(seed)
torch.manual_seed(seed)

parser = argparse.ArgumentParser(description='Training Config')

def str2bool(v):
    if isinstance(v, bool):
       return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected but got [{}].'.format(v))

# training params
parser.add_argument('--lr', type=float, default=0.01, metavar='LR',
                    help='learning rate (default: 0.01)')    # TODO(ofekp): was lr=0.005
parser.add_argument('--weight-decay', type=float, default=0.00005, metavar='WEIGHT_DECAY',
                    help='weight decay (default: 0.00005)')
parser.add_argument('--box-threshold', type=float, default=0.3, metavar='BOX_THRESHOLD',
                    help='score threshold - boxes with scores lower than specified will be ignored in training and evaluation (default: 0.3)')
parser.add_argument('--batch-size', type=int, default=12, metavar='BATCH_SIZE',
                    help='batch size (default: 12)')
parser.add_argument('--num-workers', type=int, default=4, metavar='NUM_WORKERS',
                    help='number of workers for the dataloader (default: 4)')
parser.add_argument('--num-epochs', type=int, default=4, metavar='NUM_EPOCHS',
                    help='number of epochs (default: 150)')
parser.add_argument('--model-file-suffix', type=str, default='effdet_h5py_rpn', metavar='SUFFIX',  # TODO(ofekp): default should be empty string?
                    help='Suffix to identify the model file that is saved during training (default=effdet_h5py_rpn)')
parser.add_argument('--add-user-name-to-model-file', type=str2bool, default=True, metavar='BOOL',
                    help='Will add the user name to the model file that is saved during training (default=True)')
parser.add_argument('--load-model', type=str2bool, default=True, metavar='BOOL',
                    help='Will load a model file (default=False)')
parser.add_argument('--train', type=str2bool, default=True, metavar='BOOL',
                    help='Will start training the model (default=True)')
parser.add_argument('--data-limit', type=int, default=12500, metavar='DATA_LIMIT',
                    help='Specify data limit, None to use all the data (default=12500)')
parser.add_argument('--target-dim', type=int, default=512, metavar='DIM',
                    help='Dimention of the images. It is vital that the image size will be devisiable by 2 at least 6 times (default=512)')
parser.add_argument('--h5py-dataset', type=str2bool, default=True, metavar='BOOL',
                    help='Use an H5PY dataset as created using h5py_dataset_writer.py (default=True)')
parser.add_argument('--freeze-batch-norm-weights', type=str2bool, default=True, metavar='BOOL',
                    help='Freeze batch normalization weights (default=True)')

# scheduler params
parser.add_argument('--sched-factor', type=float, default=0.01, metavar='FACTOR',
                    help='scheduler factor (default: 0.5)')
parser.add_argument('--sched-patience', type=int, default=1, metavar='PATIENCE',
                    help='scheduler patience (default: 1)')
parser.add_argument('--sched-verbose', type=str2bool, default=False, metavar='VERBOSE',
                    help='scheduler verbosity (default: False)')
parser.add_argument('--sched-threshold', type=float, default=0.0001, metavar='THRESHOLD',
                    help='scheduler threshold (default: 0.0001)')
parser.add_argument('--sched-min-lr', type=float, default=1e-8, metavar='MIN_LR',
                    help='scheduler min LR (default: 1e-8)')
parser.add_argument('--sched-eps', type=float, default=1e-08, metavar='EPS',
                    help='scheduler epsilon (default: 1e-08)')

# additional params
parser.add_argument('--gradient-accumulation-steps', type=int, default=2, metavar='NUM_EPOCHS',
                    help='number of epoch to accomulate gradients before applying back-prop (default: 2)')  # TODO(ofekp): change to 1?
parser.add_argument('--save-every', type=int, default=5, metavar='NUM_EPOCHS',
                    help='save the model every few epochs (default: 5)')
parser.add_argument('--eval-every', type=int, default=10, metavar='NUM_EPOCHS',
                    help='evaluate and print the evaluation to screen every few epochs (default: 10)')


def parse_args():
    # parse the args that are passed to this script
    args = parser.parse_args()

    # save the args as a text string so we can log them later
    args_text = yaml.safe_dump(args.__dict__, default_flow_style=False)
    return args, args_text


current_time_millis = lambda: int(round(time.time() * 1000))


def print_bold(str):
    print("\033[1m" + str + "\033[0m")


def run_os(cmd_as_list):
    process = subprocess.Popen(cmd_as_list,
                     stdout=subprocess.PIPE, 
                     stderr=subprocess.PIPE)
    stdout, stderr = process.communicate()
    stdout = stdout.strip().decode('utf-8') if stdout is not None else stdout
    stderr = stderr.strip().decode('utf-8') if stderr is not None else stderr
    return stdout, stderr


def print_nvidia_smi(device):
    if device == 'cuda:0':
        stderr, _ = run_os(['nvidia-smi', '--query-gpu=memory.used,memory.free,memory.total', '--format=csv'])
        print(stderr)


def process_data(main_folder_path, data_limit):
    allowed_classes = None  # np.array([0,1,6,9,10,20,23,24,31,32,33])

    with open(main_folder_path + '/Data/label_descriptions.json', 'r') as file:
        label_desc = json.load(file)
    sample_sub_df = pd.read_csv(main_folder_path + '/Data/sample_submission.csv')
    data_df = pd.read_csv(main_folder_path + '/Data/train.csv')
    cut_data_df = data_df
    if allowed_classes is not None:
        print("Data is limited to segments for these class ids {} entires".format(allowed_classes))
        cut_data_df = cut_data_df[cut_data_df['ClassId'].isin(allowed_classes)]
        cut_data_df['ClassId'] = cut_data_df['ClassId'].apply(lambda x: np.where(allowed_classes == x)[0][0])

    image_ids = cut_data_df['ImageId'].unique()
    image_ids_cut = image_ids

    if data_limit is not None:
        print("Data is limited to [{}] images".format(data_limit))
        image_ids_cut = image_ids[:data_limit]

    image_train_count = int(len(image_ids_cut) * 0.8)
    image_ids_train = image_ids_cut[:image_train_count]
    image_ids_test = image_ids_cut[image_train_count:]
    assert len(image_ids_cut) == (len(image_ids_test) + len(image_ids_train))
    cut_data_df = cut_data_df[cut_data_df['ImageId'].isin(image_ids_cut)]
    train_df = cut_data_df[cut_data_df['ImageId'].isin(image_ids_train)]
    test_df = cut_data_df[cut_data_df['ImageId'].isin(image_ids_test)]
    assert len(cut_data_df) == (len(test_df) + len(train_df))

    print("Train data size [{}] test data size [{}] (counting in segments)".format(len(train_df), len(test_df)))
    print()
    num_classes = None
    if allowed_classes is None:
        num_classes = len(data_df['ClassId'].unique())
    else:
        num_classes = len(allowed_classes)
    num_attributes = len(label_desc['attributes'])
    print_bold("Classes")
    categories_df = pd.DataFrame(label_desc['categories'])
    if allowed_classes is not None:
        allowed_col = categories_df['id'].isin(allowed_classes)
        allowed_col = categories_df['id'].isin(allowed_classes)
        d = {True: 'V', False: ''}
        allowed_col = allowed_col.replace(d)
        categories_df['is_allowed'] = allowed_col
    attributes_df = pd.DataFrame(label_desc['attributes'])
    print(categories_df)
    print(f'Total # of classes: {num_classes}')
    print()
    print_bold("Attributes")
    print(attributes_df.head())
    print(f'Total # of attributes: {num_attributes}')
    print()
    train_df.head()
    return num_classes, train_df, test_df, categories_df


# h5py
class DatasetH5Reader(torch.utils.data.Dataset):
    def __init__(self, in_file):
        super(DatasetH5Reader, self).__init__()
        self.in_file = in_file
        
#         assert self.h5py_file.swmr_mod
#         self.n_images, self.nx, self.ny = self.file['images'].shape

    def __getitem__(self, index):
        h5py_file = h5py.File(self.in_file, "r", swmr=True)  # swmr=True allows concurrent reads
        image = h5py_file['images'][index]
        labels = h5py_file['labels'][index]
        masks_fixed_size = h5py_file['masks'][index]
        boxes_fixed_size = h5py_file['boxes'][index]
        return image, labels, masks_fixed_size, boxes_fixed_size

    def __len__(self):
        h5py_file = h5py.File(self.in_file, "r", swmr=True)  # swmr=True allows concurrent reads
        return h5py_file['images'].shape[0]


def set_bn_eval(m):
    classname = m.__class__.__name__
    if "BatchNorm2d" in classname:
        m.affine = False
        m.weight.requires_grad = False
        m.bias.requires_grad = False
        m.eval()


def freeze_bn(model):
    model.apply(set_bn_eval)
    

def get_model_instance_segmentation(num_classes):
    from ipywidgets import FloatProgress
    # load an instance segmentation model pre-trained pre-trained on COCO
    model = torchvision.models.detection.maskrcnn_resnet50_fpn(pretrained=True)

    # get number of input features for the classifier
    in_features = model.roi_heads.box_predictor.cls_score.in_features
    # replace the pre-trained head with a new one
    model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)

    # now get the number of input features for the mask classifier
    in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
    hidden_layer = 256
    # and replace the mask predictor with a new one
    model.roi_heads.mask_predictor = MaskRCNNPredictor(in_features_mask, hidden_layer, num_classes)
    return model


class EfficientDetBB(nn.Module):

    def __init__(self, config, class_net, box_net):
        super(EfficientDetBB, self).__init__()
        self.class_net = class_net
        self.box_net = box_net

    def forward(self, x):
        '''
        Originally EfficientDet also conatined the backbone and then fpn
        but for the pupose of out network this had to be modified
        '''
        x_class = self.class_net(x)
        x_box = self.box_net(x)
        return x_class, x_box

    
class BackboneWithCustomFPN(nn.Module):
    def __init__(self, config, backbone, fpn, out_channels, alternate_init=False):
        super(BackboneWithCustomFPN, self).__init__()
        self.body = backbone
        self.fpn = fpn
        self.out_channels = out_channels
        
        for n, m in self.named_modules():
            if 'body' not in n and 'backbone' not in n:  # avoid changing the weights of the backbone which is pretrained
                if alternate_init:
                    effdet._init_weight_alt(m, n)
                else:
                    effdet._init_weight(m, n)

    def forward(self, x):
        '''
        Args:
            x - in BCHW format, e.g. x.shape = torch.Size([2, 3, 512, 512])
        '''
        x = self.body(x)  # len(x) = 3
        x = self.fpn(x)
        # at this point x is an OrderedDict of features
        return x


def get_model_instance_segmentation_efficientnet(num_classes, target_dim, freeze_batch_norm=False):
    # let's make the RPN generate 5 x 3 anchors per spatial
    # location, with 5 different sizes and 3 different aspect
    # ratios. We have a Tuple[Tuple[int]] because each feature
    # map could potentially have different sizes and
    # aspect ratios
    anchor_generator = AnchorGenerator(sizes=((32, 64, 128, 256, 512),),
#     anchor_generator = AnchorGenerator(sizes=((64, 176, 512, 288, 288),),
                                       aspect_ratios=((0.5, 1.0, 2.0),))
    
    # let's define what are the feature maps that we will
    # use to perform the region of interest cropping, as well as
    # the size of the crop after rescaling.
    # if your backbone returns a Tensor, featmap_names is expected to
    # be [0]. More generally, the backbone should return an
    # OrderedDict[Tensor], and in featmap_names you can choose which
    # feature maps to use.
    # TODO(ofekp): follow where this is being used!
    roi_pooler = torchvision.ops.MultiScaleRoIAlign(featmap_names=[0],
                                                    output_size=7,
                                                    sampling_ratio=2)
    # ofekp: note that roi_pooler is passed to box_roi_pooler in the MaskRCNN network and is not being used in roi_heads.py
    
    mask_roi_pool = MultiScaleRoIAlign(
                featmap_names=[0, 1, 2, 3],
                output_size=14,
                sampling_ratio=2)
    
    config = effdet.get_efficientdet_config('tf_efficientdet_d0')  # TODO(ofekp): use 'tf_efficientdet_d7' or try to decrease to smaller model so that we can fit 16 images in one batch
    efficientDetModelTemp = EfficientDet(config, pretrained_backbone=False)
    load_pretrained(efficientDetModelTemp, config.url)
    config.num_classes = num_classes
    config.image_size = target_dim

    out_channels = config.fpn_channels  # This is since the config of 'tf_efficientdet_d5' creates fpn outputs with num of channels = 288
    backbone_fpn = BackboneWithCustomFPN(config, efficientDetModelTemp.backbone, efficientDetModelTemp.fpn, out_channels)  # TODO(ofekp): pretrained! # from the repo trainable_layers=trainable_backbone_layers=3
    model = MaskRCNN(backbone_fpn,
                 min_size=target_dim,
                 max_size=target_dim,
                 num_classes=num_classes,
                 mask_roi_pool=mask_roi_pool,
#                  rpn_anchor_generator=anchor_generator,
                 box_roi_pool=roi_pooler)
    
    # for training with different number of classes (default is 90) we need to add this line
    # TODO(ofekp): check if we need to init weights here
    class_net = HeadNet(config, num_outputs=config.num_classes, norm_kwargs=dict(eps=.001, momentum=.01))
    efficientDetModel = EfficientDetBB(config, class_net, efficientDetModelTemp.box_net)
    model.roi_heads.box_predictor = DetBenchTrain(efficientDetModel, config)
#     # get number of input features for the classifier
#     in_features = model.roi_heads.box_predictor.cls_score.in_features
#     print(in_features)
#     # replace the pre-trained head with a new one
#     model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)
    
#     # get number of input features for the classifier
#     in_features = model.roi_heads.box_predictor.cls_score.in_features
#     # replace the pre-trained head with a new one
#     model.roi_heads.box_predictor = FastRCNNPredictor(in_features, num_classes)

#     # now get the number of input features for the mask classifier
#     in_features_mask = model.roi_heads.mask_predictor.conv5_mask.in_channels
#     hidden_layer = 256  # TODO(ofekp): was 256
#     # and replace the mask predictor with a new one
#     model.roi_heads.mask_predictor = MaskRCNNPredictor(in_features_mask, hidden_layer, num_classes)

    if freeze_batch_norm:
        # we only freeze BN layers in backbone and the BiFPN
        print("Freezing batch normalization weights")
        freeze_bn(model.backbone)

    return model


class Trainer:
    
    def __init__(self, main_folder_path, model, train_df, test_df, data_limit, num_classes, target_dim, categories_df, device, is_colab, config):
        self.main_folder_path = main_folder_path
        self.model = model
        self.train_df = train_df
        self.test_df = test_df
        self.device = device
        self.config = config
        self.num_classes = num_classes
        self.target_dim = target_dim
        self.is_colab = is_colab
        self.data_limit = data_limit
        self.optimizer = config.optimizer_class(self.model.parameters(), **config.optimizer_config)
        self.scheduler = config.scheduler_class(self.optimizer, **config.scheduler_config)
        self.model_file_path = self.get_model_file_path(is_colab, suffix=config.model_file_suffix)
        self.log_file_path = self.get_log_file_path(is_colab, suffix=config.model_file_suffix)
        self.epoch = 0
        self.visualize = visualize.Visualize(self.main_folder_path, categories_df, self.target_dim, dest_folder='Images')

        # use our dataset and defined transformations
        if self.config.h5py_dataset:
            h5_reader = imat_dataset.DatasetH5Reader("../imaterialist_" + str(self.target_dim) + ".hdf5")
            self.dataset = imat_dataset.IMATDatasetH5PY(h5_reader, self.num_classes, self.target_dim, T.get_transform(train=True))
            h5_reader_test = imat_dataset.DatasetH5Reader("../imaterialist_test_" + str(self.target_dim) + ".hdf5")
            self.dataset_test = imat_dataset.IMATDatasetH5PY(h5_reader_test, self.num_classes, self.target_dim, T.get_transform(train=False))
        else:
            self.dataset = imat_dataset.IMATDataset(self.main_folder_path, self.train_df, self.num_classes, self.target_dim, False, T.get_transform(train=True))
            self.dataset_test = imat_dataset.IMATDataset(self.main_folder_path, self.test_df, self.num_classes, self.target_dim, False, T.get_transform(train=False))
        
        # TODO(ofekp): do we need this?
        # split the dataset in train and test set
        # indices = torch.randperm(len(dataset)).tolist()
        # dataset = torch.utils.data.Subset(dataset, indices[:-50])
        # dataset_test = torch.utils.data.Subset(dataset_test, indices[-50:])

        self.log('Trainer initiallized. Device is [{}]'.format(self.device))

    def get_model_identifier(self):
        return 'dim_' + str(self.target_dim) + '_images_' + str(self.data_limit) + '_classes_' + str(self.num_classes)

    def get_model_file_path(self, is_colab, prefix=None, suffix=None):
        model_file_path = self.get_model_identifier()
        if prefix:
            model_file_path = prefix + '_' + model_file_path
        if suffix:
            model_file_path = model_file_path + '_' + suffix
            
        model_file_path = 'Model/' + model_file_path + '.model'

        if is_colab:
            model_file_path = self.main_folder_path + 'code_ofek/' + model_file_path
        else:
            model_file_path = self.main_folder_path + 'Code/' + model_file_path
        
        return model_file_path

    def get_log_file_path(self, is_colab, prefix=None, suffix=None):
        log_file_path = self.get_model_identifier()
        if prefix:
            log_file_path = prefix + '_' + log_file_path
        if suffix:
            log_file_path = log_file_path + '_' + suffix
            
        log_file_path = 'Log/' + log_file_path + '.log'

        if is_colab:
            log_file_path = self.main_folder_path + 'code_ofek/' + log_file_path
        else:
            log_file_path = self.main_folder_path + 'Code/' + log_file_path
        
        return log_file_path

    def load_model(self, device):
        if not os.path.isfile(self.model_file_path):
            self.log("Cannot load model file [{}] since it does not exist".format(self.model_file_path))
            return False
        checkpoint = torch.load(self.model_file_path, map_location=device)
        self.model.load_state_dict(checkpoint['model_state_dict'])
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        self.scheduler.load_state_dict(checkpoint['scheduler_state_dict'])
#         self.best_summary_loss = checkpoint['best_summary_loss']
        self.epoch = checkpoint['epoch'] + 1
        self.log("Loaded model file [{}]".format(self.model_file_path))
        return True
        
    def save_model(self):
        if (self.epoch) % self.config.save_every == 0:
            self.model.eval()
            torch.save({
                'model_state_dict': self.model.state_dict(),
                'optimizer_state_dict': self.optimizer.state_dict(),
                'scheduler_state_dict': self.scheduler.state_dict(),
    #             'best_summary_loss': self.best_summary_loss,
                'epoch': self.epoch,
            }, self.model_file_path)        
            self.log('Saved model to [{}]'.format(self.model_file_path))
            print_nvidia_smi(self.device)
            self.dataset_test.show_stats()

    def eval_model(self, data_loader_test):
        if (self.epoch) % self.config.eval_every == 0:
            self.model.eval()
            with torch.no_grad():
                img_idx = 1
                self.visualize.show_prediction_on_img(self.model, self.dataset_test, self.test_df, img_idx, self.is_colab, show_groud_truth=False, box_threshold=self.config.box_threshold, split_segments=True)  # TODO(ofekp): This should be test_df and dataset_test
                # evaluate on the test dataset
                engine.evaluate(self.model, data_loader_test, device=self.device)
                
    def log(self, message):
        if self.config.verbose:
            print(message)
        with open(self.log_file_path, 'a+') as logger:
            logger.write(f'{message}\n')

    def train(self):
        # define training and validation data loaders
        data_loader = torch.utils.data.DataLoader(
            self.dataset, batch_size=self.config.batch_size, shuffle=True, num_workers=self.config.num_workers,
            collate_fn=utils.collate_fn)

        data_loader_test = torch.utils.data.DataLoader(
            self.dataset_test, batch_size=self.config.batch_size, shuffle=False, num_workers=self.config.num_workers,
            collate_fn=utils.collate_fn)

        for _ in range(self.config.num_epochs):
            # tarin one epoch
            metric_logger = engine.train_one_epoch(
                self.model,
                self.optimizer,
                data_loader,
                self.device,
                self.epoch,
                gradient_accumulation_steps=self.config.gradient_accumulation_steps,
                print_freq=100,
                box_threshold=self.config.box_threshold)

            # update the learning rate
            self.scheduler.step(metric_logger.__getattr__('loss').avg)
            torch.cuda.empty_cache()  # TODO(ofekp): trying to avoid GPU memory usage increase, check that this works

            self.save_model()
            self.eval_model(data_loader_test)
            
            self.log("Epoch [{}/{}]".format(self.epoch + 1, self.config.num_epochs))
            self.epoch += 1

        self.log("Saving model one last time")
        self.save_model()
        self.eval_model(data_loader_test)
        self.log("That's it!")

        
class TrainConfig:
    def __init__(self, args):
        if args.add_user_name_to_model_file:
            self.model_file_suffix = os.getlogin() + "_" + args.model_file_suffix
        else:
            self.model_file_suffix = args.model_file_suffix
        self.h5py_dataset = args.h5py_dataset
        self.verbose = True
        self.save_every = args.save_every
        self.eval_every = args.eval_every
        self.box_threshold = args.box_threshold
        # TODO(ofekp): check if we need box_threshold_train = 0.1
        self.gradient_accumulation_steps = args.gradient_accumulation_steps
        self.batch_size = args.batch_size
        self.num_workers = args.num_workers
        self.num_epochs = args.num_epochs
        
        # optimizer = torch.optim.SGD(params, lr=0.01, momentum=0.9, weight_decay=0.00005)
        # lr_scheduler = torch.optim.lr_scheduler.StepLR(optimizer, step_size=20, gamma=0.5)
        self.optimizer_class = torch.optim.AdamW
        self.optimizer_config = dict(
            lr=args.lr,
            weight_decay=args.weight_decay
        )
        
        self.scheduler_class = torch.optim.lr_scheduler.ReduceLROnPlateau
        self.scheduler_config = dict(
            mode='min',
            factor=args.sched_factor,
            patience=args.sched_patience,
            verbose=False, 
            threshold=args.sched_threshold,
            threshold_mode='abs',
            cooldown=0, 
            min_lr=args.sched_min_lr,
            eps=args.sched_eps
        )


def main():
    args, args_text = parse_args()

    main_folder_path = "../"

    if not os.path.exists("Args"):
        os.mkdir("Args")
    with open("Args/args_text.yml", 'w') as args_file:
        args_file.write(args_text)

    # device = torch.device('cuda') if torch.cuda.is_available() else torch.device('cpu')
    isTPU = False
    forceCPU = False
    if isTPU:
        device = xm.xla_device()
    elif forceCPU:
        device = 'cpu'
    else:
        device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
    print("Device type [{}]".format(device))
    if device == 'cuda:0':
        print("Device description [{}]".format(torch.cuda.get_device_name(0)))

    
    num_classes, train_df, test_df, categories_df = process_data(main_folder_path, args.data_limit)
    print("Setting target_dim to [{}]".format(args.target_dim))

    # model = get_model_instance_segmentation(num_classes)
    model = get_model_instance_segmentation_efficientnet(num_classes, args.target_dim, freeze_batch_norm=args.freeze_batch_norm_weights)

    # get the model using our helper function
    train_config = TrainConfig(args)
    trainer = Trainer(main_folder_path, model, train_df, test_df, args.data_limit, num_classes, args.target_dim, categories_df, device, is_colab, config=train_config)

    # load a saved model
    if args.load_model:
        if not trainer.load_model(device):
            exit(1)

    if args.train:
        print_nvidia_smi(device)
        model.to(device)
        print_nvidia_smi(device)
        trainer.train()


if __name__ == '__main__':
    main()