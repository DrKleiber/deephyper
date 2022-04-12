"""This module provides tools to define hyperparameter and neural architecture search problems. Some features of this module are based on the `ConfigSpace <https://automl.github.io/ConfigSpace/master/>`_ project.
"""
import sys
import importlib

from ConfigSpace import *

class LazyImport:

    attrs = {
        "NaProblem": "_neuralarchitecture:NaProblem",
        "HpProblem": "_hyperparameter:HpProblem"

    }

    cache = {}

    def __init__(self, module_name):
        self.module_name = module_name
        self.module = sys.modules[module_name]

    def __getattr__(self, __name: str):
        
        # test cache first
        if __name in self.cache:
            return self.cache[__name]
        
        # test loadable attributes
        if __name in self.attrs:
            attr = self.attrs[__name]
            sub_module_name, attr_name = attr.split(":")
            full_module_name = f"{self.module_name}.{sub_module_name}"
            module = importlib.import_module(full_module_name)
            sys.modules[full_module_name] = module
            attr = getattr(module, attr_name)
            self.cache[__name] = attr
            return self.cache[__name]
        else:
            return self.module.__getattribute__(__name)

sys.modules[__name__] = LazyImport(__name__)