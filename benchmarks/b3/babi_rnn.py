'''Trains two recurrent neural networks based upon a story and a question.

The resulting merged vector is then queried to answer a range of bAbI tasks.

The results are comparable to those for an LSTM model provided in Weston et al.:
"Towards AI-Complete Question Answering: A Set of Prerequisite Toy Tasks"
http://arxiv.org/abs/1502.05698

Task Number                  | FB LSTM Baseline | Keras QA
---                          | ---              | ---
QA1 - Single Supporting Fact | 50               | 100.0
QA2 - Two Supporting Facts   | 20               | 50.0
QA3 - Three Supporting Facts | 20               | 20.5
QA4 - Two Arg. Relations     | 61               | 62.9
QA5 - Three Arg. Relations   | 70               | 61.9
QA6 - yes/No Questions       | 48               | 50.7
QA7 - Counting               | 49               | 78.9
QA8 - Lists/Sets             | 45               | 77.2
QA9 - Simple Negation        | 64               | 64.0
QA10 - Indefinite Knowledge  | 44               | 47.7
QA11 - Basic Coreference     | 72               | 74.9
QA12 - Conjunction           | 74               | 76.4
QA13 - Compound Coreference  | 94               | 94.4
QA14 - Time Reasoning        | 27               | 34.8
QA15 - Basic Deduction       | 21               | 32.4
QA16 - Basic Induction       | 23               | 50.6
QA17 - Positional Reasoning  | 51               | 49.1
QA18 - Size Reasoning        | 52               | 90.8
QA19 - Path Finding          | 8                | 9.0
QA20 - Agent's Motivations   | 91               | 90.7

For the resources related to the bAbI project, refer to:
https://research.facebook.com/researchers/1543934539189348

# Notes

- With default word, sentence, and query vector sizes, the GRU model achieves:
  - 100% test accuracy on QA1 in 20 epochs (2 seconds per epoch on CPU)
  - 50% test accuracy on QA2 in 20 epochs (16 seconds per epoch on CPU)
In comparison, the Facebook paper achieves 50% and 20% for the LSTM baseline.

- The task does not traditionally parse the question separately. This likely
improves accuracy and is a good example of merging two RNNs.

- The word vector embeddings are not shared between the story and question RNNs.

- See how the accuracy changes given 10,000 training samples (en-10k) instead
of only 1000. 1000 was used in order to be comparable to the original paper.

- Experiment with GRU, LSTM, and JZS1-3 as they give subtly different results.

- The length and noise (i.e. 'useless' story components) impact the ability for
LSTMs / GRUs to provide the correct answer. Given only the supporting facts,
these RNNs can achieve 100% accuracy on many tasks. Memory networks and neural
networks that use attentional processes can efficiently search through this
noise to find the relevant statements, improving performance substantially.
This becomes especially obvious on QA2 and QA3, both far longer than QA1.
'''
import sys
import os
import time

here = os.path.dirname(os.path.abspath(__file__))
top = os.path.dirname(os.path.dirname(os.path.dirname(here)))
sys.path.append(top)

start = time.time()
from functools import reduce
import re
import tarfile

import numpy as np

from keras.utils.data_utils import get_file
from keras.layers.embeddings import Embedding
from keras import layers
from keras.layers import recurrent
from keras.models import Model
from keras.preprocessing.sequence import pad_sequences

from keras import layers

from deephyper.benchmarks import keras_cmdline
from keras.models import load_model
import hashlib
import pickle

load_time = time.time() - start
print(f"module import time: {load_time:.3f} seconds")

BNAME = 'babi_rnn'

def extension_from_parameters(param_dict):
    extension = ''
    for key in sorted(param_dict):
        if key != 'epochs':
            print ('%s: %s' % (key, param_dict[key]))
            extension += '.{}={}'.format(key,param_dict[key])
    print(extension)
    return extension

def save_meta_data(data, filename):
    with open(filename, 'wb') as handle:
        pickle.dump(data, handle, protocol=pickle.HIGHEST_PROTOCOL)

def load_meta_data(filename):
    with open(filename, 'rb') as handle:
        data = pickle.load(handle)
    return data

def tokenize(sent):
    '''Return the tokens of a sentence including punctuation.

    >>> tokenize('Bob dropped the apple. Where is the apple?')
    ['Bob', 'dropped', 'the', 'apple', '.', 'Where', 'is', 'the', 'apple', '?']
    '''
    return [x.strip() for x in re.split('(\W+)?', sent) if x.strip()]


def parse_stories(lines, only_supporting=False):
    '''Parse stories provided in the bAbi tasks format

    If only_supporting is true,
    only the sentences that support the answer are kept.
    '''
    data = []
    story = []
    for line in lines:
        line = line.decode('utf-8').strip()
        nid, line = line.split(' ', 1)
        nid = int(nid)
        if nid == 1:
            story = []
        if '\t' in line:
            q, a, supporting = line.split('\t')
            q = tokenize(q)
            substory = None
            if only_supporting:
                # Only select the related substory
                supporting = map(int, supporting.split())
                substory = [story[i - 1] for i in supporting]
            else:
                # Provide all the substories
                substory = [x for x in story if x]
            data.append((substory, q, a))
            story.append('')
        else:
            sent = tokenize(line)
            story.append(sent)
    return data


def get_stories(f, only_supporting=False, max_length=None):
    '''Given a file name, read the file, retrieve the stories,
    and then convert the sentences into a single story.

    If max_length is supplied,
    any stories longer than max_length tokens will be discarded.
    '''
    data = parse_stories(f.readlines(), only_supporting=only_supporting)
    flatten = lambda data: reduce(lambda x, y: x + y, data)
    data = [(flatten(story), q, answer) for story, q, answer in data if not max_length or len(flatten(story)) < max_length]
    return data


def vectorize_stories(data, word_idx, story_maxlen, query_maxlen):
    xs = []
    xqs = []
    ys = []
    for story, query, answer in data:
        x = [word_idx[w] for w in story]
        xq = [word_idx[w] for w in query]
        # let's not forget that index 0 is reserved
        y = np.zeros(len(word_idx) + 1)
        y[word_idx[answer]] = 1
        xs.append(x)
        xqs.append(xq)
        ys.append(y)
    return pad_sequences(xs, maxlen=story_maxlen), pad_sequences(xqs, maxlen=query_maxlen), np.array(ys)


def stage_in(file_names, local_path='/local/scratch/', use_cache=True):
    if os.path.exists(local_path):
        prepend = local_path
    else:
        prepend = ''

    origin_dir_path = os.path.dirname(os.path.abspath(__file__))
    origin_dir_path = os.path.join(origin_dir_path, 'data')
    print("Looking for files:", file_names)
    print("In origin:", origin_dir_path)

    paths = {}
    for name in file_names:
        origin = os.path.join(origin_dir_path, name)
        if use_cache:
            paths[name] = get_file(fname=prepend+name, origin='file://'+origin)
        else:
            paths[name] = origin

        print(f"File {name} will be read from {paths[name]}")
    return paths


def run(param_dict):
    default_params = defaults()
    for key in default_params:
        if key not in param_dict:
            param_dict[key] = default_params[key]
    optimizer = keras_cmdline.return_optimizer(param_dict)
    print(param_dict)

    BATCH_SIZE = param_dict['batch_size']
    EPOCHS = param_dict['epochs']
    DROPOUT = param_dict['dropout']

    if param_dict['rnn_type'] == 'GRU':
        RNN = layers.GRU
    elif param_dict['rnn_type'] == 'SimpleRNN':
        RNN = layers.SimpleRNN
    else:
        RNN = layers.LSTM

    EMBED_HIDDEN_SIZE = param_dict['embed_hidden_size']
    SENT_HIDDEN_SIZE = param_dict['sent_hidden_size']
    QUERY_HIDDEN_SIZE = param_dict['query_hidden_size']

    print('RNN / Embed / Sent / Query = {}, {}, {}, {}'.format(RNN,
                                                               EMBED_HIDDEN_SIZE,
                                                               SENT_HIDDEN_SIZE,
                                                               QUERY_HIDDEN_SIZE))

    try:
        paths = stage_in(['babi-tasks-v1-2.tar.gz'], use_cache=True)
        path = paths['babi-tasks-v1-2.tar.gz']
    except:
        print('Error downloading dataset, please download it manually:\n'
              '$ wget http://www.thespermwhale.com/jaseweston/babi/tasks_1-20_v1-2.tar.gz\n'
              '$ mv tasks_1-20_v1-2.tar.gz ~/.keras/datasets/babi-tasks-v1-2.tar.gz')
        raise

    # Default QA1 with 1000 samples
    # challenge = 'tasks_1-20_v1-2/en/qa1_single-supporting-fact_{}.txt'
    # QA1 with 10,000 samples
    # challenge = 'tasks_1-20_v1-2/en-10k/qa1_single-supporting-fact_{}.txt'
    # QA2 with 1000 samples
    challenge = 'tasks_1-20_v1-2/en/qa2_two-supporting-facts_{}.txt'
    # QA2 with 10,000 samples
    # challenge = 'tasks_1-20_v1-2/en-10k/qa2_two-supporting-facts_{}.txt'
    with tarfile.open(path) as tar:
        train = get_stories(tar.extractfile(challenge.format('train')))
        test = get_stories(tar.extractfile(challenge.format('test')))

    vocab = set()
    for story, q, answer in train + test:
        vocab |= set(story + q + [answer])
    vocab = sorted(vocab)

    # Reserve 0 for masking via pad_sequences
    vocab_size = len(vocab) + 1
    word_idx = dict((c, i + 1) for i, c in enumerate(vocab))
    story_maxlen = max(map(len, (x for x, _, _ in train + test)))
    query_maxlen = max(map(len, (x for _, x, _ in train + test)))

    x, xq, y = vectorize_stories(train, word_idx, story_maxlen, query_maxlen)
    tx, txq, ty = vectorize_stories(test, word_idx, story_maxlen, query_maxlen)

    print('vocab = {}'.format(vocab))
    print('x.shape = {}'.format(x.shape))
    print('xq.shape = {}'.format(xq.shape))
    print('y.shape = {}'.format(y.shape))
    print('story_maxlen, query_maxlen = {}, {}'.format(story_maxlen, query_maxlen))


    extension = extension_from_parameters(param_dict)
    hex_name = hashlib.sha224(extension.encode('utf-8')).hexdigest()
    model_name = '{}-{}.h5'.format(BNAME, hex_name)
    model_mda_name = '{}-{}.pkl'.format(BNAME, hex_name)
    initial_epoch = 0

    resume = False

    if os.path.exists(model_name) and os.path.exists(model_mda_name):
        print('model and meta data exists; loading model from h5 file')
        model = load_model(model_name)
        saved_param_dict = load_meta_data(model_mda_name)
        initial_epoch = saved_param_dict['epochs']
        if initial_epoch < param_dict['epochs']:
            resume = True

    if not resume:
        print('Build model...')
        sentence = layers.Input(shape=(story_maxlen,), dtype='int32')
        encoded_sentence = layers.Embedding(vocab_size, EMBED_HIDDEN_SIZE)(sentence)
        encoded_sentence = layers.Dropout(DROPOUT)(encoded_sentence)

        question = layers.Input(shape=(query_maxlen,), dtype='int32')
        encoded_question = layers.Embedding(vocab_size, EMBED_HIDDEN_SIZE)(question)
        encoded_question = layers.Dropout(DROPOUT)(encoded_question)
        encoded_question = RNN(EMBED_HIDDEN_SIZE)(encoded_question)
        encoded_question = layers.RepeatVector(story_maxlen)(encoded_question)

        merged = layers.add([encoded_sentence, encoded_question])
        merged = RNN(EMBED_HIDDEN_SIZE)(merged)
        merged = layers.Dropout(DROPOUT)(merged)
        preds = layers.Dense(vocab_size, activation='softmax')(merged)

        model = Model([sentence, question], preds)
        model.compile(optimizer=optimizer, loss='categorical_crossentropy', metrics=['accuracy'])

    print('Training')
    model.fit([x, xq], y, batch_size=BATCH_SIZE, epochs=EPOCHS, validation_split=0.05)
    loss, acc = model.evaluate([tx, txq], ty, batch_size=BATCH_SIZE)
    print('Test loss / test accuracy = {:.4f} / {:.4f}'.format(loss, acc))
    print('OUTPUT:', -acc)
    
    model.save(model_name)  
    save_meta_data(param_dict, model_mda_name)
    return -acc


def augment_parser(parser):
    parser.add_argument('--rnn_type', action='store',
                        dest='rnn_type',
                        nargs='?', const=1, type=str, default='LSTM',
                        choices=['LSTM', 'GRU', 'SimpleRNN'],
                        help='type of RNN')

    parser.add_argument('--embed_hidden_size', action='store', dest='embed_hidden_size',
                        nargs='?', const=2, type=int, default='50',
                        help='number of epochs')

    parser.add_argument('--sent_hidden_size', action='store', dest='sent_hidden_size',
                        nargs='?', const=2, type=int, default='100',
                        help='number of epochs')

    parser.add_argument('--query_hidden_size', action='store', dest='query_hidden_size',
                        nargs='?', const=2, type=int, default='100',
                        help='number of epochs')                        

    return parser

def defaults():
    def_parser = keras_cmdline.create_parser()
    def_parser = augment_parser(def_parser)
    return vars(def_parser.parse_args(''))


if __name__ == "__main__":
    parser = keras_cmdline.create_parser()
    parser = augment_parser(parser)
    cmdline_args = parser.parse_args()
    param_dict = vars(cmdline_args)
    run(param_dict)
