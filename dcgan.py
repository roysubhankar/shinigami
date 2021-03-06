import os
import numpy as np
import matplotlib.pyplot as plt
from argparse import ArgumentParser
from datetime import datetime

import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision.datasets as dset
from torch.utils.data import DataLoader
import torchvision.transforms as transforms
import torchvision.utils as vutils
import torch.optim as optim
from torchvision.utils import save_image

from networks import DCGenerator, Discriminator

# custom weight initialization on the netG and netD
def weight_init(m):
    classname = m.__class__.__name__
    if classname.find('Conv') != -1:
        nn.init.normal_(m.weight.data, 0.0, 0.02)
    elif classname.find('BatchNorm') != -1:
        nn.init.normal_(m.weight.data, 1.0, 0.02)
        nn.init.constant_(m.bias.data, 0.0)

def main(args, dataloader):

    # define the G and D
    netG = DCGenerator(nz=args.nz, ngf=args.ngf, nc=args.nc).cuda()
    netG.apply(weight_init)
    print(netG)

    netD = Discriminator(nc=args.nc, ndf=args.ndf).cuda()
    netD.apply(weight_init)
    print(netD)

    # define the loss criterion
    criterion = nn.BCELoss()

    # sample a fixed noise vector that will be used to visualize the training
    # progress
    fixed_noise = torch.randn(64, args.nz, 1, 1).cuda()

    # define the ground truth labels.
    real_labels = 1  # for the real images
    fake_labels = 0  # for the fake images

    # define the optimizers, one for each network
    netD_optimizer = optim.Adam(params=netD.parameters(), lr=args.lr, betas=(0.5, 0.999))
    netG_optimizer = optim.Adam(params=netG.parameters(), lr=args.lr, betas=(0.5, 0.999))

    # sample two fixed noise vectors and do a linear interpolation between them
    # to get the intermediate noise vectors. We will generate samples for the interpolated
    # noise vectors to see effect of interpolation in the latent space. (See later!)
    z_1 = torch.randn(1, args.nz, 1, 1)
    z_2 = torch.randn(1, args.nz, 1, 1)
    fixed_interpolate = []
    for i in range(64):
        lambda_interp = i / 63
        z_interp = z_1 * (1 - lambda_interp) + lambda_interp * z_2
        fixed_interpolate.append(z_interp)
    fixed_interpolate = torch.cat(fixed_interpolate, dim=0).cuda()


    # Training loop
    iters = 0

    # for each epoch
    for epoch in range(args.num_epochs):
        # iterate through the data loader
        for i, data in enumerate(dataloader, 0):

            ## Discriminator training ##
            # maximize log(D(x)) + log(1 - D(G(x)))

            # The discriminator will be updated once with the real images
            # and once with the fake images. This is achieved by first computing
            # the gradients with the real images (the first term in the D loss function),
            # and then with the fake images generated by the G (second loss term).
            # Only after that the optimizer.step() will be done, which will update the
            # weights of the D.
            # IMPORTANT to note that when the D is updated, the G is kept frozen.
            # Gradients are calculated with loss.backward().

            # train D with real images
            netD.train()
            netD.zero_grad()
            real_images = data[0].cuda()
            bs = real_images.shape[0]
            label = torch.full((bs,), real_labels).cuda()
            noise_1 = torch.Tensor(real_images.shape).normal_(0, 0.1 * (args.num_epochs - epoch) / args.num_epochs).cuda()
            output = netD(real_images + noise_1).view(-1)
            # calculate loss on real images. It pushes the D's output for real images
            # close to 1
            errD_real = criterion(output, label)
            # calculate gradients for D
            errD_real.backward()
            # track D outputs for real images
            D_x = output.mean().item()

            # train D with fake images
            # sample a batch of noise vectors
            noise = torch.randn(bs, args.nz, 1, 1).cuda()
            # generate fake data
            fake_images = netG(noise)
            label.fill_(fake_labels)
            # run the fake images through the discriminator.
            # IMPORTANT to detach the fake_images because we do not need gradients
            # of the G activations wrt to the G weights.
            noise_2 = torch.Tensor(real_images.shape).normal_(0, 0.1 * (args.num_epochs - epoch) / args.num_epochs).cuda()
            output = netD(fake_images.detach() + noise_2).view(-1)
            # calculate loss on the fake images. It pushes the D's output for fake
            # images close to 0
            errD_fake = criterion(output, label)
            # calculate the gradients for D
            errD_fake.backward()
            errD = (errD_real + errD_fake)
            # track D outputs for fake images
            D_G_x_1 = output.mean().item()

            # update the D weights with the gradients accumulated
            netD_optimizer.step()

            ## Generator training ##
            # minimize log(1 - D(G(x)))
            # But such a formulation provides no gradient during the early stages of
            # training and hence its is reformulated as:
            # maximize log(D(G(x)))

            # during the G training the D is kept fixed
            netG.train()
            netG.zero_grad()
            # real_labels because the G wants to make the fake images look as real as
            # possible
            label.fill_(real_labels)
            output = netD(fake_images + noise_2).view(-1)
            # calculate loss for G based on the fake images. It pushes the D's output
            # for fake images close to 1
            errG = criterion(output, label)
            # calculate the gradients for G
            errG.backward()
            # track the outputs for fake images
            D_G_x_2 = output.mean().item()

            # update the G weights with the gradients accumulated
            netG_optimizer.step()

            # print the training losses
            if iters % 50 == 0:
                print('[%3d/%d][%3d/%d]\tLoss_D: %.4f\tLoss_G: %.4f\tD(x): %.4f\tD(G(z)): %.4f / %.4f'
                      % (epoch, args.num_epochs, i, len(dataloader), errD.item(), errG.item(), D_x, D_G_x_1, D_G_x_2))

            # visualize the samples generated by the G.
            if (iters % 1000 == 0):
                out_dir = os.path.join(args.log_dir, args.run_name, 'out/')
                os.makedirs(out_dir, exist_ok=True)
                interp_dir = os.path.join(args.log_dir, args.run_name, 'interpolate/')
                os.makedirs(interp_dir, exist_ok=True)
                netG.eval()
                with torch.no_grad():
                    fake_fixed = netG(fixed_noise).cpu()
                    save_image(fake_fixed, os.path.join(out_dir, str(iters).zfill(7) + '.png'),
                               normalize=True)

                    interp_fixed = netG(fixed_interpolate).cpu()
                    save_image(interp_fixed, os.path.join(interp_dir, str(iters).zfill(7) + '.png'),
                               normalize=True)

            iters += 1

if __name__ == '__main__':
    parser = ArgumentParser()
    parser.add_argument('--img_size', default=64, type=int, help='size of input image')
    parser.add_argument('--root_folder', type=str, help='path to the root folder')
    parser.add_argument('--batch_size', default=128, type=int, help='batch size')
    parser.add_argument('--nc', default=3, type=int, help='number of channels in input image')
    parser.add_argument('--ngf', default=64, type=int, help='number of generator features')
    parser.add_argument('--ndf', default=64, type=int, help='number of discriminator features')
    parser.add_argument('--nz', default='100', type=int, help='latent dimensions')
    parser.add_argument('--lr', default=0.0002, type=float, help='learning rate of the networks')
    parser.add_argument('--num_epochs', default=100, type=int, help='number of learning epochs')
    parser.add_argument('--log_dir', default='log', help='path to log directory')
    parser.add_argument('--comment', default=datetime.now().strftime('%d_%H-%M-%S'), type=str,
                        help='Comment to be appended to the model name to identify the run')
    parser.add_argument('--model_name', default='anime_small', type=str,
                        help='Name of the model you want to use.')
    args = parser.parse_args()
    args.run_name = '-'.join([args.model_name, args.comment])

    # create a set of transforms for the dataset
    dset_transforms = list()
    dset_transforms.append(transforms.Resize((args.img_size, args.img_size)))
    dset_transforms.append(transforms.ToTensor())
    dset_transforms.append(transforms.Normalize(mean=[0.5, 0.5, 0.5],
                                                std=[0.5, 0.5, 0.5]))
    dset_transforms = transforms.Compose(dset_transforms)

    # create a dataset using ImageFolder of pytorch
    dataset = dset.ImageFolder(root=args.root_folder, transform=dset_transforms)

    # create a data loader
    dataloader = DataLoader(dataset, batch_size=args.batch_size, num_workers=4, shuffle=True, drop_last=True)

    main(args, dataloader)