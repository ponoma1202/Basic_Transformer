import torch
import torchvision
import torchvision.transforms as transforms
import numpy as np
import os

from torch.utils.data import Dataset

class Mirflickr(Dataset):
    def __init__(self, root_dir, data_list=None, target_list=None, input_transform=None, target_transform=None):
        super().__init__()
        self.root_dir = root_dir 
        self.data_dir = os.path.join(root_dir, "diffuser_images_npy")
        self.target_dir = os.path.join(root_dir, "ground_truth_lensed_npy")

        if data_list == None: 
            self.data_list = os.listdir(self.data_dir)
        else:
            self.data_list = data_list
        
        if target_list == None:
            self.target_list = os.listdir(self.target_dir)
        else:
            self.target_list = target_list

        self.in_transform = input_transform
        self.target_transform = target_transform

    def __len__(self):
        return len(self.data_list)

    def __getitem__(self, index):
        image = np.load(os.path.join(self.data_dir, self.data_list[index]))
        target = np.load(os.path.join(self.target_dir, self.target_list[index]))
        img_name = self.data_list[index][:-4]      # get image name without the .npy extension

        # Normalize both input images and ground truth images to range (0, 1) using the max and min of the entire dataset
        image = (image - (-0.0079)) / (0.9004 - (-0.0079))         # (max, min) of inputs is (0.9004, -0.0079)
        target = (target - 4.1243e-05 / (1 - 4.1243e-05))          # (max, min) of targets is (1, 4.1243e-05)

        # totensor changes channel order from (H, W, C) to (C, H, W)
        if self.in_transform:
            image = self.in_transform(image)
        if self.target_transform:
            target = self.target_transform(target)

        return image, target, img_name
    

def get_loader(dataset, min_side_len, batch_size, num_workers, root_dir="/home/ponoma/workspace/DATA/mirflickr_dataset/"):
    if dataset=="CIFAR10":
        train_transform = torchvision.transforms.Compose([transforms.RandomCrop(min_side_len, padding=4), 
                                            transforms.RandomHorizontalFlip(),
                                            transforms.RandAugment(),  # RandAugment augmentation for strong regularization
                                            transforms.ToTensor(), 
                                            transforms.Normalize([0.4914, 0.4822, 0.4465], [0.2470, 0.2435, 0.2616])])          # positional embedding cannot take negative values (center around mean not around 0)                    

        val_transform = torchvision.transforms.Compose([transforms.ToTensor(),
                                                        transforms.Normalize([0.4914, 0.4822, 0.4465], [0.2470, 0.2435, 0.2616]),])
        trainset = torchvision.datasets.CIFAR10(root='./data', train=True, download=False, transform=train_transform)
        valset = torchvision.datasets.CIFAR10(root='./data', train=False, download=False, transform=val_transform)
        testset = None

    elif dataset=="Mirflickr":
        train_transform = torchvision.transforms.Compose([transforms.ToTensor(), 
                                                          transforms.CenterCrop((min_side_len, min_side_len))])                       

        val_transform = torchvision.transforms.Compose([transforms.ToTensor(), 
                                                          transforms.CenterCrop((min_side_len, min_side_len))])  

        dataset = Mirflickr(root_dir)           # Make it take in a list of 
        generator = torch.Generator().manual_seed(3)        # generator should yield deterministic behavior
        trainset, valset, testset = torch.utils.data.random_split(dataset, [0.7, 0.15, 0.15], generator)

        # Apply transforms after doing random split
        trainset = Mirflickr(root_dir, trainset.dataset.data_list, trainset.dataset.target_list, input_transform=train_transform, target_transform=train_transform)
        valset = Mirflickr(root_dir, valset.dataset.data_list, valset.dataset.target_list, val_transform, val_transform)
        testset = Mirflickr(root_dir, testset.dataset.data_list, testset.dataset.target_list, val_transform, val_transform)
        
    else:
        raise("Unkown dataset.")
    
    train_loader = torch.utils.data.DataLoader(trainset, batch_size=batch_size, shuffle=True, num_workers=num_workers)
    val_loader = torch.utils.data.DataLoader(valset, batch_size=batch_size, shuffle=False, num_workers=num_workers)
    test_loader = torch.utils.data.DataLoader(testset, batch_size=batch_size, shuffle=False, num_workers=num_workers)

    return train_loader, val_loader, test_loader
    

