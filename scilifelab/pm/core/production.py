"""Pm production module"""

import os
import re
import yaml
from cement.core import controller
from scilifelab.pm.core.controller import AbstractExtendedBaseController
from scilifelab.utils.misc import query_yes_no, filtered_walk
from scilifelab.bcbio import prune_pp_platform_args
from scilifelab.bcbio.flowcell import Flowcell
from scilifelab.bcbio.status import status_query
from scilifelab.utils.string import strip_extensions
from scilifelab.pm.core.bcbio import BcbioRunController
from scilifelab.utils.timestamp import utc_time

FINISHED_FILE = "FINISHED_AND_DELIVERED"
REMOVED_FILE = "FINISHED_AND_REMOVED"

## Main production controller
class ProductionController(AbstractExtendedBaseController, BcbioRunController):
    """
    Functionality for production management.
    """
    class Meta:
        label = 'production'
        description = 'Manage production'

    def _setup(self, base_app):
        ## Adding arguments to existing groups requires setting up parent class first
        super(ProductionController, self)._setup(base_app)
        group = [x for x in self.app.args._action_groups if x.title == 'file transfer'][0]
        group.add_argument('--from_pre_casava', help="Transfer file with move", default=False, action="store_true")
        group.add_argument('--to_pre_casava', help="Use pre-casava directory structure for delivery", action="store_true", default=False)
        group.add_argument('--transfer_dir', help="Transfer data to transfer_dir instead of sample_prj dir", action="store", default=None)
        base_app.args.add_argument('--brief', help="Output brief information from status queries", action="store_true", default=False)
    
    def _process_args(self):
        # Set root path for parent class
        ## FIXME: use abspath?
        self._meta.root_path = self.app.config.get("production", "root")
        assert os.path.exists(self._meta.root_path), "No such directory {}; check your production config".format(self._meta.root_path)
        ## Set path_id for parent class
        if self.pargs.flowcell:
            self._meta.path_id = self.pargs.flowcell
        if self.pargs.project:
            self._meta.path_id = self.pargs.project
        ## Temporary fix for pre-casava directories
        if self.pargs.from_pre_casava:
            self._meta.path_id = self.pargs.flowcell
        ## This is a bug; how will this work when processing casava-folders?!?
        ## I need to set this so as not to upset productioncontrollers process_args
        if self.command == "hs_metrics":
            self._meta.path_id = self.pargs.flowcell if self.pargs.flowcell else self.pargs.project
        super(ProductionController, self)._process_args()

    @controller.expose(hide=True)
    def default(self):
        print self._help_text

    @controller.expose(help="Query the status of flowcells, projects, samples"\
                           " that are organized according to the CASAVA file structure")
    def status_query(self):
        if not self._check_pargs(["project", "flowcell"]):
            return
        status_query(self.app.config.get("archive", "root"), self.app.config.get("production", "root"), self.pargs.flowcell, self.pargs.project, brief=self.pargs.brief)

    def _from_casava_structure(self):
        """Get information from casava structure"""
        if not self._check_pargs(["project"]):
            return
        fc_list = []
        pattern = "-bcbb-config.yaml$"
        def bcbb_yaml_filter(f):
            return re.search(pattern, f) != None
        samples = filtered_walk(os.path.join(self._meta.root_path, self._meta.path_id), bcbb_yaml_filter)
        for s in samples:
            fc = Flowcell(s)
            fc_new = fc.subset("sample_prj", self.pargs.project)
            fc_new.collect_files(os.path.dirname(s))        
            fc_list.append(fc_new)
        return fc_list
            
    def _to_casava_structure(self, fc):
        transfer_status = {}
        outdir_pfx = os.path.abspath(os.path.join(self.app.config.get("project", "root"), self.pargs.project, "data"))
        if self.pargs.transfer_dir:
           outdir_pfx = os.path.abspath(os.path.join(self.app.config.get("project", "root"), self.pargs.transfer_dir, "data"))
        for sample in fc:
            key = "{}_{}".format(sample['lane'], sample['sequence'])
            sources = {"files": self._prune_sequence_files(sample['files']), "results":sample['results']}
            outdir = os.path.join(outdir_pfx, sample['name'], fc.fc_id())
            dirs = {"data":os.path.abspath(os.path.join(outdir_pfx, sample['name'], fc.fc_id())),
                    "intermediate":os.path.abspath(os.path.join(outdir_pfx, sample['name'], fc.fc_id()))}
            self._make_output_dirs(dirs)
            fc_new = fc.subset("lane", sample['lane']).subset("name", sample['name'])
            targets = {"files": [src.replace(fc.path, dirs["data"]) for src in sources['files']],
                       "results": [src.replace(fc.path, dirs["intermediate"]) for src in sources['results']]}

            fc_new.lane_files = dict((k, [os.path.join(outdir, os.path.basename(x)) for x in v]) for k,v in fc_new.lane_files.items())
            fc_new.set_entry(key, 'files', targets['files'])
            fc_new.set_entry(key, 'results', targets['results'])
            ## Copy sample files - currently not doing lane files
            self._transfer_files(sources, targets)
            self.app.cmd.write(os.path.join(dirs["data"], "{}-bcbb-pm-config.yaml".format(sample['name'])), fc_new.as_yaml())
            transfer_status[sample['name']] = {'files':len(sources['files']), 'results':len(sources['results'])}
        ## Rewrite platform_args; only keep time, workdir, account, partition, outpath and jobname
        pattern = "-post_process.yaml$"
        def pp_yaml_filter(f):
            return re.search(pattern, f) != None
        ppfiles = filtered_walk(dirs["data"], pp_yaml_filter)
        for pp in ppfiles:
            self.app.log.debug("Rewriting platform args for {}".format(pp))
            with open(pp, "r") as fh:
                conf = yaml.load(fh)
            if not conf:
                self.app.log.warn("No configuration for {}".format(pp))
                continue
            newconf = prune_pp_platform_args(conf)
            if newconf == conf:
                continue
            self.app.cmd.safe_unlink(pp)
            self.app.cmd.write(pp, yaml.safe_dump(newconf, default_flow_style=False, allow_unicode=True, width=1000))
            
        # Write transfer summary
        self.app._output_data["stderr"].write("Transfer summary\n")
        self.app._output_data["stderr"].write("{:<18}{:>18}{:>18}\n".format("Sample","Transferred files", "Results"))
        for k, v in transfer_status.iteritems():
            self.app._output_data["stderr"].write("{:<18}{:>18}{:>18}\n".format(k, v['files'], v['results']))

    def _to_pre_casava_structure(self, fc):
        dirs = {"data":os.path.abspath(os.path.join(self.app.config.get("project", "root"), self.pargs.project, "data", fc.fc_id())),
                "intermediate":os.path.abspath(os.path.join(self.app.config.get("project", "root"), self.pargs.project, "intermediate", fc.fc_id()))}
        if self.pargs.transfer_dir:
           dirs["data"] = os.path.abspath(os.path.join(self.app.config.get("project", "root"), self.pargs.transfer_dir, "data", fc.fc_id()))
           dirs["intermediate"] = os.path.abspath(os.path.join(self.app.config.get("project", "root"), self.pargs.transfer_dir, "intermediate", fc.fc_id()))
        self._make_output_dirs(dirs)
        fc_new = fc
        for sample in fc:
            key = "{}_{}".format(sample['lane'], sample['sequence'])
            sources = {"files": self._prune_sequence_files(sample['files']), "results":sample['results']}
            targets = {"files": [src.replace(fc.path, dirs["data"]) for src in sources['files']],
                       "results": [src.replace(fc.path, dirs["intermediate"]) for src in sources['results']]}
            fc_new.set_entry(key, 'files', targets['files'])
            fc_new.set_entry(key, 'results', targets['results'])
            ## FIX ME: lane file gathering
            ## fc_new.lane_files = dict((k,[x.replace(indir, outdir) for x in v]) for k,v in fc_new.lane_files.items())
            ## Copy sample files - currently not doing lane files
            self._transfer_files(sources, targets)
        self.app.cmd.write(os.path.join(dirs["data"], "project_run_info.yaml"), fc_new.as_yaml())

    def _from_pre_casava_structure(self):
        if not self._check_pargs(["project", "flowcell"]):
            return
        fc = Flowcell()
        fc.load([os.path.join(x, self.pargs.flowcell) for x in [self.config.get("archive", "root"), self.config.get("production", "root")]])
        indir = os.path.join(self.config.get("production", "root"), self.pargs.flowcell)
        if not fc:
            self.log.warn("No run information available for {}".format(self.pargs.flowcell))
            return
        fc_new = fc.subset("sample_prj", self.pargs.project)
        fc_new.collect_files(indir)        
        return fc_new

    def _prune_sequence_files(self, flist):
        """Sometimes fastq and fastq.gz files are present for the same
        read. Make sure only one file is used, giving precedence to
        the zipped file.
        
        :param flist: list of files
        
        :returns: pruned list of files
        """
        samples = {k[0]:{} for k in [strip_extensions(x, ['.gz']) for x in flist]}
        tmp = {samples[str(k[0])].update({str(k[1]):True}) for k in [strip_extensions(x, ['.gz']) for x in flist]}
        return ["{}.gz".format(k) if v.get('.gz', None) else k for k, v in samples.items()]

    def _make_output_dirs(self, dirs):
        if not os.path.exists(dirs["data"]):
            self.app.cmd.safe_makedir(dirs["data"])
        if not os.path.exists(dirs["intermediate"]):
            self.app.cmd.safe_makedir(dirs["intermediate"])

    def _transfer_files(self, sources, targets):
        for src, tgt in zip(sources['files'] + sources['results'], targets['files'] + targets['results']):
            if not os.path.exists(os.path.dirname(tgt)):
                self.app.cmd.safe_makedir(os.path.dirname(tgt))
            self.app.cmd.transfer_file(src, tgt)

    @controller.expose(help="Transfer data")
    def transfer(self):
        if not self.pargs.from_pre_casava and self.pargs.to_pre_casava:
            self.app.log.warn("not delivering from casava input to pre_casava output")
            return
        ## Collect files depending on input structure
        if self.pargs.from_pre_casava:
            fc = self._from_pre_casava_structure()
        else:
            fc = self._from_casava_structure()
        if not fc:
            return
        ## Organize output file names depending on output structure
        if self.pargs.to_pre_casava:
            self._to_pre_casava_structure(fc)
        else:
            if isinstance(fc, list):
                for f in fc:
                    self._to_casava_structure(f)
            else:
                self._to_casava_structure(fc)

    ## Command for touching file that indicates finished samples 
    @controller.expose(help="Touch finished samples. Creates a file FINISHED_AND_DELIVERED with a utc time stamp.")
    def touch_finished(self):
        if not self._check_pargs(["project", "sample"]):
            return
        if os.path.exists(self.pargs.sample) and os.path.isfile(self.pargs.sample):
            with open(self.pargs.sample) as fh:
                slist = [x.rstrip() for x in fh.readlines()]
        else:
            slist = [self.pargs.sample]
        for s in slist:
            spath = os.path.join(self._meta.root_path, self._meta.path_id, s)
            if not os.path.exists(spath):
                self.app.log.warn("No such path {}; skipping".format(spath))
                continue
            rsync_src = os.path.join(self._meta.root_path, self._meta.path_id, s) + os.sep
            rsync_tgt = os.path.join(self.app.config.get("runqc", "root"), self.pargs.project, s) + os.sep
            cl = ["rsync {} {} {}".format(self.app.config.get("runqc", "rsync_sample_opts"), rsync_src, rsync_tgt)]
            self.app.log.info("Checking if runqc uptodate with command '{}'".format(" ".join(cl)))
            out = self.app.cmd.command(cl, **{'shell':True})
            if not self.pargs.dry_run and not out.find("total size is 0"):
                self.app.log.info("Some files need to be updated. Rsync output:")
                print "********"
                print out
                print "********"
                continue
            if not query_yes_no("Going to touch file {} for sample {}; continue?".format(FINISHED_FILE, s), force=self.pargs.force):
                continue
            self.app.log.info("Touching file {} for sample {}".format(FINISHED_FILE, s))
            with open(os.path.join(spath, FINISHED_FILE), "w") as fh:
                t_utc = utc_time()
                fh.write(t_utc)

    ## Command for removing samples that have a FINISHED_FILE flag 
    @controller.expose(help="Remove finished samples for a project. Searches for FINISHED_AND_DELIVERED and removes sample contents if file is present.")
    def remove_finished(self):
        if not self._check_pargs(["project"]):
            return
        # Don't filter out files
        def filter_fn(f):
            return True
        slist = os.listdir(os.path.join(self._meta.root_path, self._meta.path_id))
        for s in slist:
            spath = os.path.join(self._meta.root_path, self._meta.path_id, s)
            if not os.path.isdir(spath):
                continue
            if not os.path.exists(os.path.join(spath, FINISHED_FILE)):
                self.app.log.info("Sample {} not finished; skipping".format(s))
                continue
            flist = filtered_walk(spath, filter_fn)
            dlist = filtered_walk(spath, filter_fn, get_dirs=True)
            if os.path.exists(os.path.join(spath, REMOVED_FILE)):
                self.app.log.info("Sample {} already removed; skipping".format(s))
                continue
            if len(flist) > 0 and not query_yes_no("Will remove directory {} containing {} files; continue?".format(s, len(flist)), force=self.pargs.force):
                continue
            self.app.log.info("Removing {} files from {}".format(len(flist), spath))            
            for f in flist:
                if f == os.path.join(spath, FINISHED_FILE):
                    continue
                self.app.cmd.safe_unlink(f)
            self.app.log.info("Removing {} directories from {}".format(len(dlist), spath))
            for d in sorted(dlist, reverse=True):
                self.app.cmd.safe_rmdir(d)
            if not self.pargs.dry_run:
                with open(os.path.join(spath, REMOVED_FILE), "w") as fh:
                    t_utc = utc_time()
                    fh.write(t_utc)
        

