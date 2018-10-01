import logging
import os

from django.db import transaction
from django.core.exceptions import ObjectDoesNotExist
from balsam.launcher import dag
from balsam.launcher.async import FutureTask
from balsam.launcher.async import wait as balsam_wait
from balsam.core.models import ApplicationDefinition as AppDef

from deephyper.evaluators import Evaluator
logger = logging.getLogger(__name__)

LAUNCHER_NODES = int(os.environ.get('BALSAM_LAUNCHER_NODES', 1))
class BalsamEvaluator(Evaluator):
    def __init__(self, run_function, cache_key=None):
        super().__init__(run_function, cache_key)
        self.id_key_map = {}
        self.num_workers = LAUNCHER_NODES * self.WORKERS_PER_NODE
        logger.info("Balsam Evaluator instantiated")
        logger.info(f"Backend runs will use Python: {self.PYTHON_EXE}")
        self._init_app()
        logger.info(f"Backend runs will execute function: {self.appName}")
        self.transaction_context = transaction.atomic

    def wait(self, futures, timeout=None, return_when='ANY_COMPLETED'):
        return balsam_wait(futures, timeout=timeout, return_when=return_when)

    def _init_app(self):
        funcName = self._run_function.__name__
        moduleName = self._run_function.__module__
        self.appName = '.'.join((moduleName, funcName))
        try:
            app = AppDef.objects.get(name=self.appName)
        except ObjectDoesNotExist:
            logger.info(f"ApplicationDefinition did not exist for {self.appName}; creating new app in BalsamDB")
            app = AppDef(name=self.appName, executable=self._runner_executable)
            app.save()
        else:
            logger.info(f"BalsamEvaluator will use existing app {self.appName}: {app.executable}")

    def _eval_exec(self, x):
        jobname = f"task{self.counter}"
        args = f"'{self.encode(x)}'"
        envs = f"KERAS_BACKEND={self.KERAS_BACKEND}"
        resources = {
            'num_nodes': 1,
            'ranks_per_node': 1,
            'threads_per_rank': 64,
            'node_packing_count': self.WORKERS_PER_NODE,
        }
        for key in resources:
            if key in x: resources[key] = x[key]

        if dag.current_job is not None: wf = dag.current_job.workflow
        else: wf = self.appName
        task = dag.add_job(
                    name = jobname,
                    workflow = wf,
                    application = self.appName,
                    args = args,
                    environ_vars = envs,
                    **resources
                   )
        logger.debug(f"Created job {jobname}")
        logger.debug(f"Args: {args}")

        future = FutureTask(task, self._on_done, fail_callback=self._on_fail)
        future.task_args = args
        return future

    @staticmethod
    def _on_done(job, process_data):
        output = job.read_file_in_workdir(f'{job.name}.out')
        # process_data(job)
        return Evaluator._parse(output)

    @staticmethod
    def _on_fail(job):
        logger.info(f'Task {job.cute_id} failed; setting objective as float_max')
        return Evaluator.FAIL_RETURN_VALUE
