"""
Training for CycleGAN

Programmed by Aladdin Persson <aladdin.persson at hotmail dot com>
* 2020-11-05: Initial coding
* 2022-12-21: Small revision of code, checked that it works with latest PyTorch version
"""

import torch
from load_data import ObjDataset
from utils import save_checkpoint, load_checkpoint
from torch.utils.data import DataLoader
import torch.nn as nn
import torch.optim as optim
import config
from tqdm import tqdm
from torchvision.utils import save_image
from pointnet_model import Discriminator
# from generator_model import Generator
from foldingnet_model import Generator


def train_fn(
    disc_H, disc_Z, gen_Z, gen_H, loader, opt_disc, opt_gen, l1, mse, d_scaler, g_scaler
):
    H_reals = 0
    H_fakes = 0
    loop = tqdm(loader, leave=True) #Progress bar

    for idx, (zebra, horse) in enumerate(loop):
        zebra = zebra.to(config.DEVICE).float()
        horse = horse.to(config.DEVICE).float()

        # Train Discriminators H and Z
        with torch.cuda.amp.autocast(): #Necessary for float16
            fake_horse, _, _ = gen_H(zebra) #Creating fake input
            #fake_horse = fake_horse.transpose(2,1)
            # print(torch.transpose(horse,1,2).to(torch.float32))
            D_H_real = disc_H(torch.transpose(horse,1,2)) #Giving discriminator real input
            D_H_fake = disc_H(fake_horse.detach()) #Giving discriminator fake input
            H_reals += D_H_real.mean().item()
            H_fakes += D_H_fake.mean().item()
            D_H_real_loss = mse(D_H_real, torch.ones_like(D_H_real)) #MSE of D_H_real, expect 1
            D_H_fake_loss = mse(D_H_fake, torch.zeros_like(D_H_fake)) #MSE of D_H_fake, expect 0
            D_H_loss = D_H_real_loss + D_H_fake_loss #Sum of loss
            print(D_H_real_loss)

            fake_zebra, _, _ = gen_Z(horse)
            #fake_zebra = fake_zebra.transpose(2,1)
            D_Z_real = disc_Z(torch.transpose(zebra,1,2))
            D_Z_fake = disc_Z(fake_zebra.detach())
            D_Z_real_loss = mse(D_Z_real, torch.ones_like(D_Z_real))
            D_Z_fake_loss = mse(D_Z_fake, torch.zeros_like(D_Z_fake))
            D_Z_loss = D_Z_real_loss + D_Z_fake_loss

            # put it together
            D_loss = (D_H_loss + D_Z_loss) / 2

        #Standard update of weights
        opt_disc.zero_grad()
        d_scaler.scale(D_loss).backward()
        d_scaler.step(opt_disc)
        d_scaler.update()

        # Train Generators H and Z
        with torch.cuda.amp.autocast(): #Necessary for float16
            # adversarial loss for both generators
            D_H_fake = disc_H(fake_horse) #fake_horse generated by gen_H
            D_Z_fake = disc_Z(fake_zebra) #fake_zebra generated by gen_Z
            loss_G_H = mse(D_H_fake, torch.ones_like(D_H_fake)) #Real = 1, trick discriminator
            loss_G_Z = mse(D_Z_fake, torch.ones_like(D_Z_fake)) #Real = 1, trick discriminator

            # cycle loss
            fake_horse = fake_horse.transpose(2,1)
            fake_zebra = fake_zebra.transpose(2,1)
            cycle_zebra, _, _ = gen_Z(fake_horse)
            cycle_horse, _, _ = gen_H(fake_zebra)
            cycle_zebra_loss = l1(zebra, cycle_zebra.transpose(2,1))
            cycle_horse_loss = l1(horse, cycle_horse.transpose(2,1))

            # add all losses together
            G_loss = (
                loss_G_Z
                + loss_G_H
                + cycle_zebra_loss * config.LAMBDA_CYCLE
                + cycle_horse_loss * config.LAMBDA_CYCLE
            )

        opt_gen.zero_grad()
        g_scaler.scale(G_loss).backward()
        g_scaler.step(opt_gen)
        g_scaler.update()

        #if idx == idx:
        #    save_image(fake_horse * 0.5 + 0.5, f"saved_images/horse_{idx}.png")
        #    save_image(fake_zebra * 0.5 + 0.5, f"saved_images/zebra_{idx}.png")

        loop.set_postfix(H_real=H_reals / (idx + 1), H_fake=H_fakes / (idx + 1))


def main():
    #Initializing Discriminators and Generators
    disc_Z = Discriminator().to(config.DEVICE)
    disc_H = Discriminator().to(config.DEVICE)
    gen_Z = Generator().to(config.DEVICE)
    gen_H = Generator().to(config.DEVICE)
    opt_disc = optim.Adam(
        list(disc_H.parameters()) + list(disc_Z.parameters()),
        lr=config.LEARNING_RATE,
        betas=(0.5, 0.999),
    )
    opt_gen = optim.Adam(
        list(gen_Z.parameters()) + list(gen_H.parameters()),
        lr=config.LEARNING_RATE,
        betas=(0.5, 0.999),
    )

    L1 = nn.L1Loss() #Cycle consistensy loss and Identity loss
    mse = nn.MSELoss() #Adverserial loss

    if config.LOAD_MODEL: #True/False defined in config
        load_checkpoint(
            config.CHECKPOINT_GEN_H,
            gen_H,
            opt_gen,
            config.LEARNING_RATE,
        )
        load_checkpoint(
            config.CHECKPOINT_GEN_Z,
            gen_Z,
            opt_gen,
            config.LEARNING_RATE,
        )
        load_checkpoint(
            config.CHECKPOINT_CRITIC_H,
            disc_H,
            opt_disc,
            config.LEARNING_RATE,
        )
        load_checkpoint(
            config.CHECKPOINT_CRITIC_Z,
            disc_Z,
            opt_disc,
            config.LEARNING_RATE,
        )

    #Create dataset
    dataset = ObjDataset(
        root_male=config.TRAIN_DIR + "/male", 
        root_female=config.TRAIN_DIR + "/female",
        transform=config.transforms,
        n_points=config.N_POINTS
    )
    val_dataset = ObjDataset(
        root_male=config.VAL_DIR + "/male",
        root_female=config.VAL_DIR + "/female",
        transform=config.transforms,
        n_points=config.N_POINTS
    )

    val_loader = DataLoader( val_dataset, batch_size=config.BATCH_SIZE, shuffle=True, pin_memory=True)
    loader = DataLoader(dataset, batch_size=config.BATCH_SIZE, shuffle=True, num_workers=config.NUM_WORKERS, pin_memory=True)
    
    g_scaler = torch.cuda.amp.GradScaler() #Scaler to run in float 16, if removed we run in float 32
    d_scaler = torch.cuda.amp.GradScaler() #Scaler to run in float 16, if removed we run in float 32

    for epoch in range(config.NUM_EPOCHS):
        train_fn(
            disc_H,
            disc_Z,
            gen_Z,
            gen_H,
            loader,
            opt_disc,
            opt_gen,
            L1,
            mse,
            d_scaler,
            g_scaler,
        )

        #Save model for every epoch 
        if config.SAVE_MODEL:
            save_checkpoint(gen_H, opt_gen, filename=config.CHECKPOINT_GEN_H)
            save_checkpoint(gen_Z, opt_gen, filename=config.CHECKPOINT_GEN_Z)
            save_checkpoint(disc_H, opt_disc, filename=config.CHECKPOINT_CRITIC_H)
            save_checkpoint(disc_Z, opt_disc, filename=config.CHECKPOINT_CRITIC_Z)


if __name__ == "__main__":
    main()