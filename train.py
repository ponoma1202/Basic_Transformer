import torch
import torchvision
from tqdm import tqdm
import os
import wandb

from model import Transformer
from utils import Rescale
from utils import TransformerScheduler

os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID" 
os.environ["CUDA_VISIBLE_DEVICES"] = '0,1,2'
gpu_number = 1

def main():
    # Using CIFAR 10 for the data

    batch_size = 64
    num_epochs = 100
    learning_rate = 1e-5
    num_classes = 10
    num_heads = 8
    num_blocks = 6
    d_model = 128 # 512                                                                                       # dimension of hidden layer in Transformer
    use_scheduler = False
    save_path = './checkpoint/model.pth'
    class_names = ['plane', 'car', 'bird', 'cat', 'deer', 'dog', 'frog', 'horse', 'ship', 'truck']      # For confusion matrix
    run = wandb.init(project='basic_transformer', config={"learning_rate":learning_rate,
                                                    "architecture": Transformer,
                                                    "dataset": 'CIFAR-10',
                                                    "epochs":num_epochs,
                                                    "batch_size":batch_size,
                                                    "classes":num_classes,
                                                    "num_heads":num_heads,
                                                    "num_encoder_layers": num_blocks,
                                                    "use_scheduluer":use_scheduler,
                                                    "d_model":d_model})

    transform = torchvision.transforms.Compose([torchvision.transforms.ToTensor(),                  # issue: scales image to [0,1] range          
                                                # do not want to center around 0. center around mean instead to avoid negative values (for embedding)
                                                torchvision.transforms.Normalize((0, 0, 0), (0.247, 0.243, 0.261)),       # from https://github.com/kuangliu/pytorch-cifar/issues/19  
                                                torchvision.transforms.RandomRotation(45),
                                                torchvision.transforms.RandomHorizontalFlip(),
                                                torchvision.transforms.RandomVerticalFlip(),
                                                torchvision.transforms.Grayscale(), 
                                                Rescale()])      # resolve scaling issue from ToTensor (embedding only takes in ints, so we need [0, 255] range)      
                                        
    trainset = torchvision.datasets.CIFAR10(root='./data', train=True, download=False, transform=transform)
    
    trainloader = torch.utils.data.DataLoader(trainset, batch_size=batch_size, shuffle=True)

    testset = torchvision.datasets.CIFAR10(root='./data', train=False, download=False, transform=transform)
    testloader = torch.utils.data.DataLoader(testset, batch_size=batch_size, shuffle=False)

    in_dim = 256                        # Number of embeddings in input sequence (size of input vocabulary). Pixels have range [0, 255]
    out_dim = num_classes               # Number of embeddings in output sequence. Each image is labeled with a single class (10 total classes)
    
    # See if gpu is available
    if torch.cuda.is_available():
        device = torch.device("cuda")
        torch.cuda.set_device(gpu_number) 
    else:
        device = torch.device("cpu")
    
    # Initialize model and move to GPU if available
    model = Transformer(in_dim, out_dim, device, num_heads, num_blocks, d_model)
    model.to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)           
    criterion = torch.nn.CrossEntropyLoss()
    scheduler = None
    if use_scheduler:
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(optimizer) 
        #scheduler = TransformerScheduler(optimizer, d_model)

    if not os.path.exists(save_path):          
        os.mkdir(save_path)

    train(model, trainloader, testloader, optimizer, criterion, num_epochs, device, save_path, class_names, scheduler)
    run.finish()


# Includes both training and validation
def train(model, trainloader, testloader, optimizer, criterion, num_epochs, device, save_path, class_names, scheduler):
    for epoch in range(num_epochs):
        print(f'Start training epoch {epoch+1}/{num_epochs}...')
        train_accuracy, train_loss = train_epoch(model, epoch, num_epochs, trainloader, optimizer, criterion, device) 
        val_acc, val_loss = validate(model, testloader, criterion, device, save_path, class_names)
        wandb.log({"training_accuracy":train_accuracy, "training_loss":train_loss, "validation_acc":val_acc, "validation_loss":val_loss, "epoch":epoch, "learning rate":optimizer.param_groups[-1]['lr']})
    if scheduler != None:
        scheduler.step()
    torch.save(model.state_dict(), save_path)
        

def train_epoch(model, epoch, num_epochs, trainloader, optimizer, criterion, device):
    model.train()
    total_correct = 0
    total_loss = 0

    for step, batch in enumerate(tqdm(trainloader)):
        input, target = batch
        input, target = input.to(device), target.to(device)
        output = model(input, target)                                                    # result is a (num_classes, batch_size) tensor
        optimizer.zero_grad()
        loss = criterion(output.squeeze(), target)                                       # take argmax to get the class with the highest "probability"
        loss.backward()
        optimizer.step() 
        pred = output.squeeze().argmax(dim=1)                                           # output is (batch_size, target seq len, num_classes) so need to squeeze to (batch_size, num_classes). For classification, target seq len = 1                             # get batch size
        total_loss += loss.item()
        total_correct += (pred == target).sum().item()                                  # summing over a list results in a list so need to use .item() to get a number.
        pred, target = pred.to("cpu").numpy(), target.to("cpu").numpy()                 # Need to convert to numpy arrays and move to cpu for wandb confusion matrix

    accuracy = total_correct / len(trainloader.dataset)
    avg_loss = total_loss/ len(trainloader.dataset)
    print(f'Epoch {epoch+1}/{num_epochs}, Loss: {avg_loss}, Accuracy: {accuracy}')
    return accuracy, avg_loss          


def validate(model, testloader, criterion, device, save_path, class_names, load=False):
    if load:
        model.load_state_dict(torch.load(save_path))                         # if loading from saved model
    
    model.eval()
    print("Starting validation...")
    with torch.no_grad():
        total_correct = 0
        total_loss = 0.0
        all_targets = [] #torch.tensor([], device=device)
        all_preds = [] #torch.tensor([], device=device)

        for step, batch in enumerate(tqdm(testloader)):
            input, target = batch
            input, target = input.to(device), target.to(device)
            output = model(input, target)
            loss = criterion(output.squeeze(), target)                                  # Need to .squeeze() because of headed attention.
            pred = output.squeeze().argmax(dim=1)
            total_loss += loss
            total_correct += (pred == target).sum().item()

            # accumulate all targets and preds and then run confusion matrix
            all_targets.extend(target.cpu().numpy())
            all_preds.extend(pred.cpu().numpy())
            # all_targets = torch.cat((all_targets, target), dim=0)
            # all_preds = torch.cat((all_preds, pred), dim=0)

        wandb.log({'confusion_mat' : wandb.sklearn.plot_confusion_matrix(all_targets, all_preds, class_names)})
        wandb.log({"conf_mat" : wandb.plot.confusion_matrix(probs=None,                 # Track confusion matrix to see accuracy for each class
                        y_true=all_targets, preds=all_preds,
                        class_names=class_names)})
        accuracy = total_correct/len(testloader.dataset)
        avg_loss = total_loss/len(testloader.dataset)
        print(f'Validation Loss: {avg_loss}, Validation Accuracy: {accuracy} \n')
        return accuracy, avg_loss
    

if __name__ == "__main__":
    main()
