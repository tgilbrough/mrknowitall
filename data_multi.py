import json, math
import numpy as np
import os
import nltk
nltk.download('punkt')

import tensorflow as tf

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

class Data:
    def __init__(self, config):
        self.batch_size = config.batch_size
        self.keep_prob = config.keep_prob
        self.valBatchNum = 0

        print('Preparing embedding matrix.')

        # load training data, parse, and split
        print('Loading in training data...')
        trainData = self.importMsmarco(config.train_path)
        self.tContext, self.tQuestion, self.tQuestionID, self.tAnswerBegin, self.tAnswerEnd, self.tAnswerText, \
            self.maxLenTContext, self.maxLenTQuestion, tPassageLengths = self.splitMsmarcoDatasets(trainData)

        # load validation data, parse, and split
        print('Loading in validation data...')
        valData = self.importMsmarco(config.val_path)
        self.vContext, self.vQuestion, self.vQuestionID, self.vAnswerBegin, self.vAnswerEnd, self.vAnswerText, \
            self.maxLenVContext, self.maxLenVQuestion, vPassageLengths = self.splitMsmarcoDatasets(valData)

        self.tPassRel = self.passageRevelevance(self.tContext, self.tQuestion, tPassageLengths)
        self.vPassRel = self.passageRevelevance(self.vContext, self.vQuestion, vPassageLengths)

        print('Building vocabulary...')
        # build a vocabulary over all training and validation context paragraphs and question words
        vocab = self.buildVocab(self.tContext + self.tQuestion + self.vContext + self.vQuestion)

        # Reserve 0 for masking via pad_sequences
        self.vocab_size = len(vocab) + 1
        word_index = dict((c, i + 1) for i, c in enumerate(vocab))
        self.max_context_size = max(self.maxLenTContext, self.maxLenVContext)
        self.max_ques_size = max(self.maxLenTQuestion, self.maxLenVQuestion)

        self.tPassWeights = self.passageWeight(self.tPassRel, tPassageLengths, self.max_context_size)
        self.vPassWeights = self.passageWeight(self.vPassRel, vPassageLengths, self.max_context_size)

        # Note: Need to download and unzip Glove pre-train model files into same file as this script
        embeddings_index = self.loadGloveModel('./datasets/glove/glove.6B.' + str(config.emb_size) + 'd.txt')
        self.embeddings = self.createEmbeddingMatrix(embeddings_index, word_index)

        # vectorize training and validation datasets
        print('Begin vectorizing process...')

        # tX: training Context, tXq: training Question, tYBegin: training Answer Begin ptr,
        # tYEnd: training Answer End ptr
        self.tX, self.tXq, self.tYBegin, self.tYEnd = self.vectorizeData(self.tContext, self.tQuestion,
                                                                        self.tAnswerBegin, self.tAnswerEnd, word_index)
        self.vX, self.vXq, self.vYBegin, self.vYEnd = self.vectorizeData(self.vContext, self.vQuestion,
                                                                        self.vAnswerBegin, self.vAnswerEnd, word_index)

        print('Vectorizing process completed.')

        self.convertToNumpy()

    def getAllData(self):
        return self.all_data

    def convertToNumpy(self):
        self.tX = np.array(self.tX)
        self.tXq = np.array(self.tXq)
        self.tYBegin = np.array(self.tYBegin)
        self.tYEnd = np.array(self.tYEnd)
        self.tPassRel = np.array(self.tPassRel)
        self.tPassWeights = np.array(self.tPassWeights)
        self.vX = np.array(self.vX)
        self.vXq = np.array(self.vXq)
        self.vYBegin = np.array(self.vYBegin)
        self.vYEnd = np.array(self.vYEnd)
        self.vPassRel = np.array(self.vPassRel)
        self.vPassWeights = np.array(self.vPassWeights)
        self.vContext = np.array(self.vContext, dtype=object)
        self.vQuestionID = np.array(self.vQuestionID, dtype=object)
        

    def getNumTrainBatches(self):
        return int(math.ceil(len(self.tX) / self.batch_size))

    def getNumValBatches(self):
        return int(math.ceil(len(self.vX) / self.batch_size))

    def getRandomTrainBatch(self):
        points = np.random.choice(len(self.tX), self.batch_size)

        tX_batch = self.tX[points]
        tXq_batch = self.tXq[points]
        tYBegin_batch = self.tYBegin[points]
        tYEnd_batch = self.tYEnd[points]
        tPassWeights_batch = self.tPassWeights[points]

        return {'tX': tX_batch, 'tXq': tXq_batch,
                'tYBegin': tYBegin_batch, 'tYEnd': tYEnd_batch, 
                'tXPassWeights': tPassWeights_batch}

    def getRandomValBatch(self):
        points = np.random.choice(len(self.vX), self.batch_size)

        vX_batch = self.vX[points]
        vXq_batch = self.vXq[points]
        vYBegin_batch = self.vYBegin[points]
        vYEnd_batch = self.vYEnd[points]
        vPassWeights_batch = self.vPassWeights[points]

        return {'vX': vX_batch, 'vXq': vXq_batch,
                'vYBegin': vYBegin_batch, 'vYEnd': vYEnd_batch,
                'vXPassWeights': vPassWeights_batch}

    def getValBatch(self):
        start = self.valBatchNum * self.batch_size
        end = min(len(self.vX), (self.valBatchNum + 1) * self.batch_size)
        points = np.arange(start, end)

        vContext_batch = self.vContext[points]
        vQuestionID_batch = self.vQuestionID[points]
        vX_batch = self.vX[points]
        vXq_batch = self.vXq[points]
        vYBegin_batch = self.vYBegin[points]
        vYEnd_batch = self.vYEnd[points]
        vPassWeights_batch = self.vPassWeights[points]

        self.valBatchNum += 1

        if self.valBatchNum >= self.getNumValBatches():
            self.valBatchNum = 0

        return {'vContext': vContext_batch, 'vQuestionID': vQuestionID_batch,
                'vX': vX_batch, 'vXq': vXq_batch,
                'vYBegin': vYBegin_batch, 'vYEnd': vYEnd_batch, 
                'vXPassWeights': vPassWeights_batch}

    def loadGloveModel(self, gloveFile):
        print("Loading Glove Model...")
        f = open(gloveFile,'r', encoding='utf-8')
        embedding_index = {}
        for line in f:
            values = line.split()
            word = values[0]
            coefs = np.asarray(values[1:], dtype='float32')
            embedding_index[word] = coefs
        f.close()
        print('Found %s word vectors.' % len(embedding_index))
        return embedding_index

    def createEmbeddingMatrix(self, embeddings_index, word_index):
        dim_size = len(embeddings_index['a'])
        # +1 is for <PAD>
        embedding_matrix = np.zeros((len(word_index) + 1, dim_size))
        for word, i in word_index.items():
            embedding_vector = embeddings_index.get(word)
            if embedding_vector is not None:
                # words not found in embedding index will be all-zeros.
                embedding_matrix[i] = embedding_vector
        return embedding_matrix

    def tokenize(self, sent):
        '''Return the tokens of a context including punctuation. Wrapper around nltk.word_tokenize to
           fix weird quotation marks.
        '''
        return [token.replace("``", '"').replace("''", '"') for token in nltk.word_tokenize(sent)]

    def join(self, sent):
        def join_punctuation(seq, characters='.,;?!'):
            characters = set(characters)
            seq = iter(seq)
            current = next(seq)

            for nxt in seq:
                if nxt in characters:
                    current += nxt
                else:
                    yield current
                    current = nxt

            yield current
        return ' '.join(join_punctuation(sent))

    

    def splitMsmarcoDatasets(self, f):
        '''Given a parsed Json data object, split the object into training context (paragraph), question, answer matrices,
           and keep track of max context and question lengths.
        '''
        xContext = [] # list of contexts paragraphs
        xQuestion = [] # list of questions
        xQuestion_id = [] # list of question id
        xAnswerBegin = [] # list of indices of the beginning word in each answer span
        xAnswerEnd = [] # list of indices of the ending word in each answer span
        xAnswerText = [] # list of the answer text
        xPassageLengths = [] # list of the passage lengths
        maxLenContext = 0
        maxLenQuestion = 0

        # For now only pick out selected passages that have answers directly inside the passage
        for data in f['data']:
            passages = []
            passage_lengths = []
            for passage in data['passages']:
                context = passage['passage_text']
                contextTokenized = self.tokenize(context.lower())
                passage_lengths.append(len(contextTokenized))
                passages += contextTokenized

            contextLength = len(passages)
            if contextLength > maxLenContext:
                maxLenContext = contextLength

            question = data['query']
            questionTokenized = self.tokenize(question.lower())
            if len(questionTokenized) > maxLenQuestion:
                maxLenQuestion = len(questionTokenized)

            question_id = data['query_id']

            for answer in data['answers']:
                answerTokenized = self.tokenize(answer.lower())
                answerBeginIndex, answerEndIndex = self.findAnswer(passages, answerTokenized)
                if answerBeginIndex != -1:
                    xContext.append(passages)
                    xQuestion.append(questionTokenized)
                    xQuestion_id.append(question_id)
                    xAnswerBegin.append(answerBeginIndex)
                    xAnswerEnd.append(answerEndIndex)
                    xAnswerText.append(answer)
                    xPassageLengths.append(passage_lengths)
                    break

        return xContext, xQuestion, xQuestion_id, xAnswerBegin, xAnswerEnd, xAnswerText, maxLenContext, maxLenQuestion, xPassageLengths

    def findAnswer(self, contextTokenized, answerTokenized):
        contextLen = len(contextTokenized)
        answerLen = len(answerTokenized)
        for i in range(contextLen - answerLen + 1):
            match = sum([1 for j, m in zip(contextTokenized[i:i + answerLen], answerTokenized) if j == m])
            if match == answerLen:
                return (i, i + answerLen + 1)
        return (-1, -1)

    def vectorizeData(self, xContext, xQuestion, xAnswerBegin, xAnswerEnd, word_index):
        '''Converts context and question words to their respective index and pad context to max context length
           and question to max question length. *Convert answers to one-hot vectors of length max context.
        '''
        X = []
        Xq = []
        YBegin = []
        YEnd = []
        for i in range(len(xContext)):
            x = [word_index[w] for w in xContext[i]]
            xq = [word_index[w] for w in xQuestion[i]]
            # map the first and last words of answer span to one-hot representations
            y_Begin =  xAnswerBegin[i]
            y_End = xAnswerEnd[i] - 1
            X.append(x)
            Xq.append(xq)
            YBegin.append(y_Begin)
            YEnd.append(y_End)
        return self.pad_sequences(X, self.max_context_size), self.pad_sequences(Xq, self.max_ques_size), YBegin, YEnd

    def pad_sequences(self, X, maxlen):
        for context in X:
            for i in range(maxlen - len(context)):
                context.append(0)
        return X

    def passageRevelevance(self, xContext, xQuestion, xPassageLengths):
        cs = []
        for i in range(len(xContext)):
            passages = []
            prev_end = 0
            for j in xPassageLengths[i]:
                passages.append(' '.join(xContext[i][prev_end : prev_end + j]))
                prev_end += j

            tfidf = TfidfVectorizer().fit_transform([' '.join(xQuestion[i])] + passages)
            cosine_similarities = linear_kernel(tfidf[0:1], tfidf).flatten()[1:]
            
            # Apply softmax
            cosine_similarities = [math.exp(x) for x in cosine_similarities]
            sum_cs = sum(cosine_similarities)
            cosine_similarities = [x / sum_cs for x in cosine_similarities]
            
            cs.append(cosine_similarities)
        
        return cs

    def passageWeight(self, passRel, passLen, max_len):
        ret = []
        for i in range(len(passLen)):
            passWeight = []
            for r, l in zip(passRel[i], passLen[i]):
                for _ in range(l):
                    passWeight.append(r)

            for i in range(max_len - len(passWeight)):
                passWeight.append(0.0)

            ret.append(passWeight)
        return ret

    def saveAnswersForEval(self, questionType, candidateName, vContext, vQuestionID, predictedBegin, predictedEnd, trueBegin, trueEnd):
        ref_fn = './references/' + questionType + '.json'
        can_fn = './candidates/' + candidateName + '.json'
        
        rf = open(ref_fn, 'w', encoding='utf-8')
        cf = open(can_fn, 'w', encoding='utf-8')

        for i in range(len(vContext)):
            predictedAnswer = ' '.join(vContext[i][predictedBegin[i] : predictedEnd[i] + 1])
            trueAnswer = ' '.join(vContext[i][trueBegin[i] : trueEnd[i] + 1])

            reference = {}
            candidate = {}
            reference['query_id'] = vQuestionID[i]
            reference['answers'] = [trueAnswer]

            candidate['query_id'] = vQuestionID[i]
            candidate['answers'] = [predictedAnswer]

            print(json.dumps(reference, ensure_ascii=False), file=rf)
            print(json.dumps(candidate, ensure_ascii=False), file=cf)

        rf.close()
        cf.close()

    def importMsmarco(self, json_file):
        data = {}
        data['data'] = []
        with open(json_file, encoding='utf-8') as f:
            data['data'] = [json.loads(line) for line in f]
        return data

    def buildVocab(self, sentences):
        '''Accepts a list of list of words. For example, a list of contexts or questions that are tokenized.
           Returns a sorted list of strings that comprise the vocabulary.
        '''
        vocab = set([word for words in sentences for word in words])
        vocab = sorted(vocab)
        return vocab