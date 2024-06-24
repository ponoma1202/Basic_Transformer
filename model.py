import torch
from torch import nn
import math

# based on https://github.com/brandokoch/attention-is-all-you-need-paper/tree/master and pytorch tutorial

class Transformer(nn.Module):
    def __init__(self, img_side_len, patch_size, n_channels, num_classes, device, n_heads=8, n_blocks=6, embed_dim=512, d_ffn=2048, dropout_rate=0.1):
        super().__init__()
        self.embed_dim = embed_dim
        self.positional_encoding = Patch_Embedding(img_side_len, patch_size, n_channels, embed_dim, device, dropout_rate) #Positional_Encoding(in_dim, embed_dim, device)         # input positional encoding for encoder

        # MLP head from ViT applied to class token
        self.mlp_head = nn.Linear(embed_dim, num_classes)          # linear layer to get output classes
        self.tanh = nn.Tanh()                              

        self.encoder = Encoder(embed_dim, d_ffn, n_heads, n_blocks, dropout_rate, device)
        self.apply(self.init_weights)

    # Initialize weights to very small numbers close to 0, instead of pytorch's default initalization. 
    def init_weights(self, m):
        if isinstance(m, nn.Linear) or isinstance(m, nn.Conv2d):
            torch.nn.init.normal_(m.weight, std=0.001)
        if isinstance(m, Patch_Embedding):
            torch.nn.init.trunc_normal_(m.class_token, mean=0.0, std=0.02)       # taken from https://github.com/s-chh/PyTorch-Scratch-Vision-Transformer-ViT-MNIST-CIFAR10/blob/main/model.py 
            torch.nn.init.trunc_normal_(m.pos_encoding, mean=0.0, std=0.02)
    
    def forward(self, x, target):                                                       
        encoder_in = self.positional_encoding(x)         # (batch_size, seq_len, embed_dim) tensor
        encoder_output = self.encoder(encoder_in)        # output = (batch_size, num_tokens, embed_dim)

        # Take out class token and run MLP head only on class token
        class_token_learned = encoder_output[:, 0, :]
        class_token_learned = self.tanh(class_token_learned)
        output = self.mlp_head(class_token_learned)     
        return output
    
# list of all encoder blocks
class Encoder(nn.Module):
    def __init__(self, embed_dim, d_ffn, n_heads, n_blocks, dropout_rate, device):
        super().__init__()

        self.encoder_layers = nn.ModuleList([Encoder_Block(embed_dim, d_ffn, n_heads, dropout_rate, device) for _ in range(n_blocks)])

    def forward(self, x):
        for encoder_layer in self.encoder_layers:
            x = encoder_layer(x)     # call on individual encoder block one at a time.
        return x
    
# list of all decoder blocks
class Encoder_Block(nn.Module):
    def __init__(self, embed_dim, d_ffn, n_heads, dropout_rate, device):
        super().__init__()
        embed_dim = embed_dim
        d_ffn = d_ffn                 # feed forward network layer dim ~ 4 times the size of embed_dim
        dropout_rate = dropout_rate

        self.attention = Multi_Headed_Attention(n_heads, embed_dim, device)    
        self.dropout1 = nn.Dropout(dropout_rate)
        self.add_and_norm1 = Add_and_Norm(embed_dim)     

        self.ffn = Position_wise_ffn(embed_dim, d_ffn)        # output has dim embed_dim
        self.dropout2 = nn.Dropout(dropout_rate)
        self.add_and_norm2 = Add_and_Norm(embed_dim)

    def forward(self, x):
        attention_layer = self.attention(x)
        attention_layer = self.dropout1(attention_layer) 
        x = self.add_and_norm1(attention_layer, x)       # x is original input embedding

        ffn = self.ffn(x)
        ffn = self.dropout2(ffn)
        ffn = self.add_and_norm2(x, ffn)
        return ffn
    

### Helper classes ###

class Multi_Headed_Attention(nn.Module):
    def __init__(self, n_heads, embed_dim, device):
        super().__init__()
        self.n_heads = n_heads

        # If doesn't divide evenly, concatenation won't work.
        assert embed_dim % n_heads == 0
        self.d_key = embed_dim // n_heads

        self.Wq = nn.Linear(embed_dim, embed_dim)     
        self.Wk = nn.Linear(embed_dim, embed_dim)     
        self.Wv = nn.Linear(embed_dim, embed_dim)
        self.attention = Self_Attention(device)

    def forward(self, x, is_masked=False):
        q = self.Wq(x)      # dimensions = (batch_size, seq_len, embed_dim) for Q, K, V
        k = self.Wk(x)
        v = self.Wv(x)

        # 1 - split into heads
        q = q.view(q.size(0), q.size(1), self.n_heads, self.d_key)     # (batch size, sequence len, num heads, d_key)
        k = k.view(k.size(0), k.size(1), self.n_heads, self.d_key)
        v = v.view(v.size(0), v.size(1), self.n_heads, self.d_key)

        # 2 - swap n_heads and sequence length dimensions to split each batch along each head
        q = q.transpose(1, 2)             # (batch size, n_heads, sequence len, d_key)
        k = k.transpose(1, 2)
        v = v.transpose(1, 2)

        # 3 - pass into self-attention layer
        output = self.attention(q, k, v, is_masked)

        # 4 - Reverse step 2 then concatenate heads
        output = output.transpose(1, 2).contiguous()            # Need contiguous because .view() changes the way the tensor is stored (not stored consecutively in memory anymore)
        output = output.view(output.size(0), -1, self.d_key * self.n_heads)
        return output


class Self_Attention(nn.Module):        # q and k have dim (d_v, d_k)
    def __init__(self, device):
        super().__init__()  
        self.device = device      

    def forward(self, q, k, v, is_masked, padding=0):
        d_k = q.size(-1)                # get last dimension of q (should be d_k)
        padding = padding                                                                            
        attention_weights = torch.matmul(q, k.transpose(-2, -1)) / math.sqrt(d_k)       # want last two dimensions to get swapped
        
        # source/padding mask. Safer to make this into a boolean mask rather than rounding so that numbers won't accidentally be rounded to 0
        mask = attention_weights != padding                                                                # make sure padding values are not considered in softmax

        # target mask (for decoder)
        if is_masked:                       
            # combine padding and target mask. Should have dimensions (target_sequence_len, target_sequence_len)
            mask = torch.tril(torch.ones([1, attention_weights.size(-1), attention_weights.size(-1)], device=self.device)).bool() & mask    # target sequence length for dim = -1 should be the same as dim = -2   
        
        attention_weights = attention_weights.masked_fill(mask == 0, -1e9)                                  # set all values we want to ignore to -infinity
        
        probabilities = torch.softmax(attention_weights, dim=-1)                                            # gets the probabilities along last dimension. For 2d the result of softmax is a (d_v, 1) vector.
        return torch.matmul(probabilities, v)  


# separate image into patches and do positional embedding + patch embedding for ViT
# Followed: 
class Patch_Embedding(nn.Module):
    def __init__(self, img_side_len, patch_size, n_channels, embed_dim, device, dropout_rate=0.1):
        super().__init__()
        self.patch_size = patch_size
        self.embed_dim = embed_dim          # same as embedding dimension 
        self.num_patches = (img_side_len // patch_size) ** 2 + 1          # add 1 for the class token
        
        # Note: positional embedding in ViT does not use sine/cosine
        self.conv = torch.nn.Conv2d(n_channels, embed_dim, kernel_size=patch_size, stride=patch_size)         # no overlapping means stride needs to be same as patch size
        self.pos_encoding = nn.Parameter(torch.zeros([1, self.num_patches, self.embed_dim], requires_grad=True))
        self.class_token = nn.Parameter(torch.zeros([1, 1, self.embed_dim], requires_grad=True)) 
        self.dropout = nn.Dropout(dropout_rate)

    def forward(self, x):
        x = self.conv(x)        # (batch size, channels, height, width) => (batch size, embed_dim, height/patch size, width/patch size)
        x = torch.flatten(x, 2)         # result = (batch size, embed_dim, num_patches). 
        x = x.transpose(1, 2)           # switch to having num_patch tensors of dim embed_dim (embedding dimension)
        
        class_token = self.class_token.expand(x.size(0), -1, -1)            # add in batch dim
        x = torch.cat((class_token, x), dim=1)

        x = x + self.pos_encoding
        x = self.dropout(x)
        return x


# Followed: https://pytorch.org/tutorials/beginner/transformer_tutorial.html#:~:text=class%20PositionalEncoding(nn.Module)%3A 
class Positional_Encoding(nn.Module):                     
    def __init__(self, in_dim, embed_dim, device, dropout_rate=0.1, max_len=5000):
        super().__init__()
        self.embed_dim = embed_dim
        self.dropout = nn.Dropout(dropout_rate)
        self.flatten = nn.Flatten() 
        self.in_embedding = nn.Embedding(in_dim, embed_dim) 
        self.class_token = nn.Parameter(torch.zeros(1, 1, embed_dim)) 
        self.pos_encoding = torch.zeros([1, max_len, embed_dim], device=device)                       # each "word" has encoding of size embed_dim

        # calculate e^(2i * log(n)/embed_dim) where n = 10000 from original paper and i goes from 0 to embed_dim/2 because there are embed_dim PAIRS
        div_term = torch.exp(torch.arange(0, embed_dim, 2) * -(math.log(torch.tensor(10000.0)) / embed_dim))  

        # create (max_len, 1) column tensor for all positions (numbered)
        pos = torch.arange(0, max_len).unsqueeze(1)

        # broadcast and set even indices to sin and odd indices to cos
        self.pos_encoding[0, :, 0::2] = torch.sin(pos * div_term)                  # select all rows. Start at column 0 and skip every 2 cols
        self.pos_encoding[0, :, 1::2] = torch.cos(pos * div_term)    

        # make positional embedding a parameter so it can be learned
        self.pos_encoding = nn.Parameter(self.pos_encoding)             

    def forward(self, x):
        x = self.flatten(x).to(dtype=torch.long, device=x.device)
        class_token = self.class_token.expand(x.size(0), -1, -1)
        embedded_img = self.in_embedding(x) * math.sqrt(self.embed_dim)
        x = torch.cat((class_token, embedded_img), dim=1)               # concatenate the class token with the flattened image embedding along num_tokens dimension (dim = 1)

        x = x + self.pos_encoding[:, :x.size(1)]                                   # trim down pos_encoding to size of actual input sequence. dim = (1, num_tokens, embed_dim)
        x = self.dropout(x)                          
        return x

# called "position wise" because it already includes positional embeddings from previous layers
class Position_wise_ffn(nn.Module):                           # 2 fully connected dense layers  https://medium.com/@hunter-j-phillips/position-wise-feed-forward-network-ffn-d4cc9e997b4c 
    def __init__(self, embed_dim, d_ffn):                       # feed forward just means no recurrent relations
        super().__init__()
        self.linear1 = nn.Linear(embed_dim, d_ffn)
        self.linear2 = nn.Linear(d_ffn, embed_dim)
        self.gelu = nn.GELU()
    
    def forward(self, x):
        x = self.linear1(x)         
        x = self.gelu(x)
        x = self.linear2(x)
        return x    

class Add_and_Norm(nn.Module):
    def __init__(self, embed_dim):
        super().__init__()
        self.norm = nn.LayerNorm(embed_dim)

    def forward(self, input, output):
        return self.norm(input + output)