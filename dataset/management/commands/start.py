from django.core.management.base import BaseCommand
import urllib
import urllib2
import json
import os
import os.path
import zipfile
import logging
import numpy as np
from StringIO import StringIO
from dataset import models
import requests, requests_cache
from django.core.exceptions import ObjectDoesNotExist
from optparse import make_option


logging.basicConfig(  
    level = logging.INFO,
    format = '[%(levelname)s, L:%(lineno)d] %(message)s',
)  

species_map = {'Homo sapiens':'human', 'Mus musculus':'mouse', 'Rattus norvegicus':'rat','Drosophila melanogaster':'fruitfly', \
               'Caenorhabditis elegans':'nematode', 'Danio rerio':'zebrafish', 'Arabidopsis thaliana':'thale-cress',\
               'Xenopus tropicalis':'frog', 'Sus scrofa':'pig'}
work_dir = {'base':'tmp/', 'sample':'tmp/sample/', 'unzip':'tmp/unzip_sample/'}
base_url = "http://www.ebi.ac.uk/arrayexpress/json/v2/"
requests_cache.install_cache('arrayexpress_cache')

class Command(BaseCommand):

    option_list = BaseCommand.option_list+(make_option("-a", "--arrays", action="store", type="string", dest="array_file", help='Specify file containing array types.',),)
    option_list = option_list+(make_option("-s", "--skip", action="store", type="string", dest="skip_file", help='Specify file containing array types to skip, only effect with -a',),)
    option_list = option_list+(make_option("-t", "--test", action="store", type="string", dest="test", help='Test the specified experiment. No database writing.',),)
    option_list = option_list+(make_option("-e", "--exp", action="store", type="string", dest="exp", help='Load the specified experiment.',),)

    def load_experiment(self, e, line):
        dataset = get_exp_info(e)
        get_exp_sample_file(e)
        logging.debug('setup_dataset')
        data_matrix = setup_dataset(e)
        #print data_matrix
        logging.info('write database')
        #platform
        try:
            pf = models.BiogpsDatasetPlatform.objects.get(platform=line)
        except ObjectDoesNotExist:
            pf = models.BiogpsDatasetPlatform.objects.create(platform=line, reporters=data_matrix.keys())
        #dataset
        meta = {'geo_gds_id':'', 'name':dataset['name'], 'factors':{}, 'default':False, 'display_params':{}, \
                 'summary':dataset['summary'], 'source':base_url+"experiments/" + e, \
                 'geo_gse_id':e, 'pubmed_id':dataset['pubmed_id'], 'owner':'ArrayExpress Uploader', 'geo_gpl_id':line,\
                 'secondaryaccession':dataset['secondaryaccession'], 'factors':dataset['factors']}
        try:
            ds = models.BiogpsDataset.objects.get(geo_gse_id=e)
            ds.delete()
        except ObjectDoesNotExist:
            pass                 
        ds = models.BiogpsDataset.objects.create(name=dataset['name'], 
                                             summary=dataset['summary'],
                                             ownerprofile_id='arrayexpress_sid',
                                             platform=pf,
                                             geo_gds_id='',
                                             geo_gse_id=e,
                                             geo_id_plat=e+'_'+line,
                                             metadata=meta,
                                             species=species_map[dataset['species']])
        #dataset data
        datasetdata = []
        for reporter in data_matrix:                        
            datasetdata.append(models.BiogpsDatasetData(dataset=ds, reporter=reporter, data=data_matrix[reporter]))
        models.BiogpsDatasetData.objects.bulk_create(datasetdata)
        ds_matrix = np.array(data_matrix.values(), np.float32)
        #tmp file
        s = StringIO()
        np.save(s, ds_matrix)
        s.seek(0)
        #dataset matrix
        mat = models.BiogpsDatasetMatrix(dataset=ds, reporters=data_matrix.keys(), matrix=s.read())
        mat.save()
        #finish, mark as loaded
        models.BiogpsDatasetGeoLoaded.objects.create(geo_type=e, with_platform=line, dataset=ds)

    def handle(self, *args, **options):
        
        #create directory for download and parse usage
        if not os.path.exists(work_dir['base']):
            os.makedirs(work_dir['base'])
            os.makedirs(work_dir['sample'])
            os.makedirs(work_dir['unzip'])

        if options['test'] is not None:
            logging.info('test experiment %s ...'%options['test'])
            dataset = get_exp_info(options['test'])
            get_exp_sample_file(options['test'])
            data_matrix = setup_dataset(options['test'])
            #print data_matrix
            logging.info('test over')
        elif options['array_file'] is not None:
            skip_exps = []
            if options['skip_file'] is not None:
                with open(options['skip_file'], 'r') as skipfile:
                    raw = skipfile.readlines()
                    for s in raw:
                        str = s.split('#')[0].strip()
                        if str != '':
                            skip_exps.append(str)
            with open(options['array_file'], 'r') as file:
                line = file.readline().strip()
                while line != '':
                    logging.info('---process Array type: %s ---'%(line))
                    #current_platform['platform'] = line
                    exps = get_arraytype_exps(line)
                    logging.info('%d experiments in total'%(len(exps)))
                    if not len(exps)>0:
                        raise Exception, 'no experiment for this array type'
                    #process each exps for this array type
                    for e in exps:
                        if e in skip_exps:
                            logging.info('-skip experiment %s, it\'s in skip file-'%e)
                            continue
                        logging.info('-process experiment %s-'%e)
                        try:
                            models.BiogpsDatasetGeoLoaded.objects.get(geo_type=e, with_platform=line)
                            logging.info('already loaded, skip')
                            continue
                        except Exception:
                            pass
                        self.load_experiment(e, line)
                    line = file.readline().strip()
        elif options['exp'] is not None:
            print 'NOT implemented'
            return
            self.load_experiment(options['exp'], 'aaa')

#from array type, get its experiment set
def get_arraytype_exps(array_type):    
    url = base_url+"files?array=" + array_type
    explist = []
    logging.info('get all experiment IDs')
    logging.info('connect to %s'%(url))
#     conn = urllib2.urlopen(url)
#     data = conn.read()
#     data_json = json.loads(data)
    res = requests.get(url)
    data_json = res.json()
        
    if data_json["files"]["total-experiments"] > 0:
        experiments = data_json["files"]["experiment"]
        for experiment in experiments:
            accession = experiment["accession"]
            explist.append(accession)
    else:
        return ()
    return tuple(explist)

def get_exp_info(exp):
    url = base_url+"experiments/" + exp
    dataset = {}
    logging.info('get experiment info from %s'%(url))
#     conn = urllib2.urlopen(url)
#     data = conn.read()
#     data_json = json.loads(data)
    res = requests.get(url)
    data_json = res.json()
    dataset['name'] = data_json["experiments"]["experiment"]["name"]
    dataset['summary'] = data_json["experiments"]["experiment"]["description"]["text"]
    dataset['species'] = data_json["experiments"]["experiment"]["organism"]
    try:
        dataset['secondaryaccession'] = data_json["experiments"]["experiment"]["secondaryaccession"]
    except Exception,e:
        dataset['secondaryaccession'] = ''
    
    try:
        dataset['pubmed_id'] = data_json["experiments"]["experiment"]["bibliography"]["accession"]
    except Exception,e:
        dataset['pubmed_id'] = ''
    #get experiment factorsd
    url = base_url+"files/" + exp
    logging.info('get experiment file info from %s'%(url))
#     conn = urllib2.urlopen(url)
#     data = conn.read()
#     data_json = json.loads(data)
    res = requests.get(url)
    data_json = res.json()
    files = data_json["files"]["experiment"]["file"]
    dataset['factors'] = []
    for file in files:
        if file["kind"] == u'sdrf':
            logging.info('get experiment sdrf file from %s'%(file["url"]))
#             conn = urllib2.urlopen(file["url"])
#             data = conn.read()
            res = requests.get(url)
            data = res.text
            header = data.split('\n')[0]
            filter = parse_sdrf_header(header)
            data = data.split('\n')[1:]
            for d in data:
                if d == '':
                    continue
                factor = {'factorvalue':{}, 'comment':{}, 'characteristics': {}}
                cel = d.split('\t')
                for k in filter['factorvalue']:
                    factor['factorvalue'][k] = cel[filter['factorvalue'][k]]
                for k in filter['comment']:
                    factor['comment'][k] = cel[filter['comment'][k]]
                for k in filter['characteristics']:
                    factor['characteristics'][k] = cel[filter['characteristics'][k]]
                dataset['factors'].append({cel[0]:factor})
    return dataset

def parse_sdrf_header(header):
    headers = header.split('\t')
    res = {'characteristics':{}, 'comment':{}, 'factorvalue':{}}
    i = 0
    while i<len(headers):
        h = headers[i]
        if h.find('Characteristics')==0:
            key = h.split('[')[1].split(']')[0]
            res['characteristics'][key] = i
        if h.find('Comment')==0:
            key = h.split('[')[1].split(']')[0]
            res['comment'][key] = i
        if h.find('Factor')==0:
            key = h.split('[')[1].split(']')[0]
            res['factorvalue'][key] = i
        i += 1
    return res

#get all data for the experiment and set up data in database
def get_exp_sample_file(exp):

    url = base_url+"files/" + exp
    logging.info('get experiment file info from %s'%(url))
#     conn = urllib2.urlopen(url)
#     data = conn.read()
#     data_json = json.loads(data)
    res = requests.get(url)
    data_json = res.json()
    experiment = data_json["files"]["experiment"]
    if isinstance(experiment, list):
        files = experiment[0]["file"]
    else:
        files = experiment["file"]
    for file in files:
        if file["kind"] == u'processed':
            dest = "tmp/sample/" + file["name"]
            if not os.path.exists(dest):
                logging.info('get sample file: %s'%(file["url"]))
                urllib.urlretrieve(file["url"], dest)
                unzip_file("tmp/sample/" + file["name"], "tmp/unzip_sample/" + exp)
            else:                
                logging.info('sample file exists')
            #setup_dataset(exp) 
    logging.debug('leave get_exp_sample_file')

#setup data from file downloaded
def setup_dataset(exp):  
    path = 'tmp/unzip_sample/' + exp
    dir = os.listdir(path)
    dir.sort()
    data_matrix = {}
    for f in dir:
        with open(path+'/'+f, 'r') as file:
            line = file.readline().strip()
            first_line = True
            ending = len(line.split('\t'))
            while line != '':
                splited = line.split('\t')
                #check format, and skip first line
                if first_line:                    
                    first_line = False
                    line = file.readline().strip()
                    #E-GEOD-4006 style, skp 2 lines
                    if splited[0] == 'Scan REF':
                        line = file.readline()
                    #E-MTAB-1169 style, skp 2 lines
                    elif splited[0] == 'Hybridization REF':
                        line = file.readline()
                    #E-GEOD-26688 style, skip columns after first 2
                    elif len(splited)>2 and splited[2] == 'ABS_CALL':
                        ending = 2                    
                    continue
                if len(splited)<=1:
                    line = file.readline().strip()
                    continue
                #make sure data is digital
                i = 1
                while i<ending:
                    try:
                        splited[i] = float(splited[i])
                        i += 1
                    except ValueError, e:
                        raise Exception, 'file format wrong, check columns of file:%s'%(path+'/'+f)
                reporter = splited[0]
                if reporter in data_matrix:
                    data_matrix[reporter].extend(splited[1:ending])
                else:
                    data_matrix[reporter] = splited[1:ending]
                line = file.readline().strip()
            return data_matrix
    return data_matrix


def unzip_file(zipfilename, unziptodir):
    if not os.path.exists(unziptodir):
        os.mkdir(unziptodir, 0777)
    zfobj = zipfile.ZipFile(zipfilename)
    for name in zfobj.namelist():
        name = name.replace('\\', '/')
        if name.endswith('/'):
            os.mkdir(os.path.join(unziptodir, name))
        else:
            ext_filename = os.path.join(unziptodir, name)
            ext_dir = os.path.dirname(ext_filename)
            if not os.path.exists(ext_dir):
                os.mkdir(ext_dir, 0777)
            outfile = open(ext_filename, 'wb')
            outfile.write(zfobj.read(name))
            outfile.close()
