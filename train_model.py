import os

from common  import *
from model import *
from dataset import *
from torchcontrib.optim import SWA
import torch.cuda.amp as amp
is_amp = True 

def get_learning_rate(optimizer):
    return optimizer.param_groups[0]['lr']

def do_valid(net, valid_loader):

    valid_num = 0
    valid_probability = []
    valid_mask = []
    valid_loss = 0

    net = net.eval()
    start_timer = time.time()

    for t, batch in enumerate(valid_loader):

        net.output_type = ['loss', 'inference']
        with torch.no_grad():
            with amp.autocast(enabled = is_amp):

                batch_size = len(batch['index'])
                batch['image'] = batch['image'].cuda()
                batch['mask' ] = batch['mask' ].cuda()
                batch['organ'] = batch['organ'].cuda()

                output = net(batch)
                
                loss0  = output['bce_loss'].mean()

        valid_probability.append(output['probability'].data.cpu().numpy())
        valid_mask.append(batch['mask'].data.cpu().numpy())
        valid_num += batch_size
        valid_loss += batch_size*loss0.item()
        #DiceVal(output['probability'].cpu(), batch['mask'].cpu())
        
        #debug
        if 0 :
            pass
            organ = batch['organ'].data.cpu().numpy()
            image = batch['image']
            mask  = batch['mask']
            probability  = output['probability']

            for b in range(batch_size):
                m = tensor_to_image(image[b])
                t = tensor_to_mask(mask[b,0])
                p = tensor_to_mask(probability[b,0])
                overlay = result_to_overlay(m, t, p )

                text = label_to_organ[organ[b]]
                draw_shadow_text(overlay,text,(5,15),0.7,(1,1,1),1)

                image_show_norm('overlay',overlay,min=0,max=1,resize=1)
                cv2.waitKey(0)


    probability = np.concatenate(valid_probability)
    mask = np.concatenate(valid_mask)

    loss = valid_loss/valid_num   #np_binary_cross_entropy_loss(probability, mask)

    dice = compute_dice_score(probability, mask) #compute_dice_score(probability, mask)


    dice = dice.mean()

    return [dice, loss,  0, 0]


def run_train():
    fold = 3

    out_dir =  'kaggle/working/result/run20/segformer-mit-b2-aux5-768/fold-%d' % (fold)
    initial_checkpoint = None # out_dir + '/checkpoint/00001925.model.pth'  #
    #None #

    start_lr   = 5e-5 #0.0001
    batch_size = 4 #32 #32

    for f in ['checkpoint','train','valid','backup'] : os.makedirs(out_dir +'/'+f, exist_ok=True)

    log = open(out_dir+'/log.train.txt',mode='a')

    train_df, valid_df = make_fold(fold)

    train_dataset = CustomDataset(train_df, "train", train_augment5b) #HubmapDataset(train_df, train_augment5b)
    valid_dataset = CustomDataset(valid_df, "test", valid_augment5) #HubmapDataset(valid_df, valid_augment5)
    
    
    train_loader  = DataLoader(
        train_dataset,
        sampler = RandomSampler(train_dataset),
        batch_size  = batch_size,
        drop_last   = True,
        num_workers = 2,
        pin_memory  = False,
        worker_init_fn = lambda id: np.random.seed(torch.initial_seed() // 2 ** 32 + id),
        collate_fn = null_collate,
    )

    valid_loader = DataLoader(
        valid_dataset,
        sampler = SequentialSampler(valid_dataset),
        batch_size  = 1,
        drop_last   = False,
        num_workers = 2,
        pin_memory  = False,
        collate_fn = null_collate,
    )



    log.write('fold = %s\n'%str(fold))
    log.write('train_dataset : \n%s\n'%(train_dataset))
    log.write('valid_dataset : \n%s\n'%(valid_dataset))
    log.write('\n')

    ## net ----------------------------------------
    log.write('** net setting **\n')

    scaler = amp.GradScaler(enabled = is_amp)
    net = init_model() #Net().cuda()

    log.write('\tinitial_checkpoint = %s\n' % initial_checkpoint)
    log.write('\n')


    ## optimiser ----------------------------------
    if 0: ##freeze
        for p in net.stem.parameters():   p.requires_grad = False
        pass

    def freeze_bn(net):
        for m in net.modules():
            if isinstance(m, nn.BatchNorm2d):
                m.eval()
                m.weight.requires_grad = False
                m.bias.requires_grad = False
    #freeze_bn(net)

    #-----------------------------------------------

    optimizer = torch.optim.AdamW(filter(lambda p: p.requires_grad, net.parameters()), lr=start_lr)
    optimizer = SWA(optimizer, 2000, 700, 0.00005)
    #optimizer = Lookahead(RAdam(filter(lambda p: p.requires_grad, net.parameters()),lr=start_lr), alpha=0.5, k=5)

    
    log.write('optimizer\n  %s\n'%(optimizer))
    log.write('\n')

    num_iteration = 1000*len(train_loader)
    iter_log   = len(train_loader)*1 #479
    iter_valid = iter_log
    iter_save  = iter_log

    ## start training here! ##############################################
    #array([0.57142857, 0.42857143])
    log.write('** start training here! **\n')
    log.write('   batch_size = %d \n'%(batch_size))

    log.write('                     |-------------- VALID---------|---- TRAIN/BATCH ----------------\n')
    log.write('rate     iter  epoch | dice   loss   tp     tn     | loss           | time           \n')
    log.write('-------------------------------------------------------------------------------------\n')

              #0.00100   0.50  0.80 | 0.891  0.020  0.000  0.000  | 0.000  0.000   |  0 hr 02 min

    def message(mode='print'):
        asterisk = ' '
        if mode==('print'):
            loss = batch_loss
        if mode==('log'):
            loss = train_loss
            if (iteration % iter_save == 0): asterisk = '*'

        text = \
        ('%0.2e   %08d%s %6.2f | '%(rate, iteration, asterisk, epoch,)).replace('e-0','e-').replace('e+0','e+') + \
        '%4.3f  %4.3f  %4.4f  %4.3f   | '%(*valid_loss,) + \
        '%4.3f  %4.3f   | '%(*loss,) + \
        '%s' % (datetime.timedelta(seconds=int(time.time() - start_timer)))
        #print("\n")
        
        
        return text

    #----
    valid_loss = np.zeros(4,np.float32)
    train_loss = np.zeros(2,np.float32)
    batch_loss = np.zeros_like(train_loss)
    sum_train_loss = np.zeros_like(train_loss)
    sum_train = 0

    start_iteration = 0
    start_epoch = 0


    start_timer = time.time()
    iteration = start_iteration
    epoch = start_epoch
    rate = 0
    while iteration < num_iteration:
        for t, batch in enumerate(train_loader):


            if iteration%iter_save==0:
                if iteration != start_iteration and iteration > 1400:
                    if iteration > 4549:
                        optimizer.swap_swa_sgd()
                    torch.save({
                        'state_dict': net.state_dict(),
                        'iteration': iteration,
                        'epoch': epoch,
                    }, out_dir + '/checkpoint/%08d.model.pth' %  (iteration))
                    pass


            if (iteration%iter_valid==0): # or (t==len(train_loader)-1):
                #if iteration!=start_iteration:
                valid_loss = do_valid(net, valid_loader)  #
                pass


            if (iteration%iter_log==0) or (iteration%iter_valid==0):
                #print('\r', end='', flush=True)
                print('\n')
                log.write(message(mode='log') + '\n')


            # learning rate schduler ------------
            # adjust_learning_rate(optimizer, scheduler(epoch))
            rate = get_learning_rate(optimizer) #scheduler.get_last_lr()[0] #get_learning_rate(optimizer)

            # one iteration update  -------------
            batch_size = len(batch['index'])
            batch['image'] = batch['image'].cuda()
            batch['mask' ] = batch['mask' ].cuda()
            batch['organ'] = batch['organ'].cuda()


            net.train()
            net.output_type = ['loss']
            #with torch.autograd.set_detect_anomaly(True):
            if 1:
                with amp.autocast(enabled = is_amp):
                    output = net(batch)
                    loss0  = output['bce_loss'].mean()
                    loss1  = output['aux2_loss'].mean()
                #loss1  = output['lovasz_loss'].mean()

                optimizer.zero_grad()
                scaler.scale(loss0+0.2*loss1).backward()

                scaler.unscale_(optimizer)
                #torch.nn.utils.clip_grad_norm_(net.parameters(), 2)
                scaler.step(optimizer)
                scaler.update()


            # print statistics  --------
            batch_loss[:2] = [loss0.item(),loss1.item()]
            sum_train_loss += batch_loss
            sum_train += 1
            if t % 100 == 0:
                train_loss = sum_train_loss / (sum_train + 1e-12)
                sum_train_loss[...] = 0
                sum_train = 0

            print('\r', end='', flush=True)
            print(message(mode='print'), end='', flush=True)
            epoch += 1 / len(train_loader)
            iteration += 1

        torch.cuda.empty_cache()
    log.write('\n')

if __name__ == '__main__':
	run_train()