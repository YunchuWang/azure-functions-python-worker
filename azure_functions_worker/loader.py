# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License.
"""Python functions loader."""
import importlib
import importlib.machinery
import importlib.util
import os
import os.path
import pathlib
import sys
import time
from datetime import timedelta
from os import PathLike, fspath
from typing import Optional, Dict

from google.protobuf.duration_pb2 import Duration

from . import protos, functions
from .bindings.retrycontext import RetryPolicy
from .constants import MODULE_NOT_FOUND_TS_URL, SCRIPT_FILE_NAME, \
    PYTHON_LANGUAGE_RUNTIME, RETRY_POLICY, CUSTOMER_PACKAGES_PATH
from .logging import logger
from .utils.wrappers import attach_message_to_exception

_AZURE_NAMESPACE = '__app__'
_DEFAULT_SCRIPT_FILENAME = '__init__.py'
_DEFAULT_ENTRY_POINT = 'main'
_submodule_dirs = []


def register_function_dir(path: PathLike) -> None:
    try:
        _submodule_dirs.append(fspath(path))
    except TypeError as e:
        raise RuntimeError(f'Path ({path}) is incompatible with fspath. '
                           f'It is of type {type(path)}.', e)


def install() -> None:
    if _AZURE_NAMESPACE not in sys.modules:
        # Create and register the __app__ namespace package.
        ns_spec = importlib.machinery.ModuleSpec(_AZURE_NAMESPACE, None)
        ns_spec.submodule_search_locations = _submodule_dirs
        ns_pkg = importlib.util.module_from_spec(ns_spec)
        sys.modules[_AZURE_NAMESPACE] = ns_pkg


def convert_to_seconds(timestr: str):
    x = time.strptime(timestr, '%H:%M:%S')
    return int(timedelta(hours=x.tm_hour, minutes=x.tm_min,
                         seconds=x.tm_sec).total_seconds())


def uninstall() -> None:
    pass


def build_binding_protos(indexed_function) -> Dict:
    binding_protos = {}
    for binding in indexed_function.get_bindings():
        binding_protos[binding.name] = protos.BindingInfo(
            type=binding.type,
            data_type=binding.data_type,
            direction=binding.direction)

    return binding_protos


def build_retry_protos(indexed_function) -> Dict:
    retry = get_retry_settings(indexed_function)

    if not retry:
        return None

    strategy = retry.get(RetryPolicy.STRATEGY.value)
    max_retry_count = int(retry.get(RetryPolicy.MAX_RETRY_COUNT.value))
    retry_strategy = retry.get(RetryPolicy.STRATEGY.value)

    if strategy == "fixed_delay":
        return build_fixed_delay_retry(retry, max_retry_count, retry_strategy)
    else:
        return build_variable_interval_retry(retry, max_retry_count,
                                             retry_strategy)


def get_retry_settings(indexed_function):
    try:
        return indexed_function.get_settings_dict(RETRY_POLICY)
    except AttributeError as e:
        logger.warning("AttributeError while loading retry policy. %s", e)
        return None


def build_fixed_delay_retry(retry, max_retry_count, retry_strategy):
    delay_interval = Duration(
        seconds=convert_to_seconds(retry.get(RetryPolicy.DELAY_INTERVAL.value))
    )
    return protos.RpcRetryOptions(
        max_retry_count=max_retry_count,
        retry_strategy=retry_strategy,
        delay_interval=delay_interval,
    )


def build_variable_interval_retry(retry, max_retry_count, retry_strategy):
    minimum_interval = Duration(
        seconds=convert_to_seconds(
            retry.get(RetryPolicy.MINIMUM_INTERVAL.value))
    )
    maximum_interval = Duration(
        seconds=convert_to_seconds(
            retry.get(RetryPolicy.MAXIMUM_INTERVAL.value))
    )
    return protos.RpcRetryOptions(
        max_retry_count=max_retry_count,
        retry_strategy=retry_strategy,
        minimum_interval=minimum_interval,
        maximum_interval=maximum_interval
    )


def process_indexed_function(functions_registry: functions.Registry,
                             indexed_functions):
    fx_metadata_results = []
    for indexed_function in indexed_functions:
        function_info = functions_registry.add_indexed_function(
            function=indexed_function)

        binding_protos = build_binding_protos(indexed_function)
        retry_protos = build_retry_protos(indexed_function)

        function_metadata = protos.RpcFunctionMetadata(
            name=function_info.name,
            function_id=function_info.function_id,
            managed_dependency_enabled=False,  # only enabled for PowerShell
            directory=function_info.directory,
            script_file=indexed_function.function_script_file,
            entry_point=function_info.name,
            is_proxy=False,  # not supported in V4
            language=PYTHON_LANGUAGE_RUNTIME,
            bindings=binding_protos,
            raw_bindings=indexed_function.get_raw_bindings(),
            retry_options=retry_protos,
            properties={"worker_indexed": "True"})

        fx_metadata_results.append(function_metadata)

    return fx_metadata_results


@attach_message_to_exception(
    expt_type=ImportError,
    message=f'Please check the requirements.txt file for the missing module. '
            f'For more info, please refer the troubleshooting'
            f' guide: {MODULE_NOT_FOUND_TS_URL} ',
    debug_logs='Error in load_function. '
               f'Sys Path: {sys.path}, Sys Module: {sys.modules},'
               'python-packages Path exists: '
               f'{os.path.exists(CUSTOMER_PACKAGES_PATH)}')
def load_function(name: str, directory: str, script_file: str,
                  entry_point: Optional[str]):
    dir_path = pathlib.Path(directory)
    script_path = pathlib.Path(script_file) if script_file else pathlib.Path(
        _DEFAULT_SCRIPT_FILENAME)
    if not entry_point:
        entry_point = _DEFAULT_ENTRY_POINT

    register_function_dir(dir_path.parent)

    try:
        rel_script_path = script_path.relative_to(dir_path.parent)
    except ValueError:
        raise RuntimeError(
            f'script path {script_file} is not relative to the specified '
            f'directory {directory}'
        )

    last_part = rel_script_path.parts[-1]
    modname, ext = os.path.splitext(last_part)
    if ext != '.py':
        raise RuntimeError(
            f'cannot load function {name}: '
            f'invalid Python filename {script_file}')

    modname_parts = [_AZURE_NAMESPACE]
    modname_parts.extend(rel_script_path.parts[:-1])

    # If the __init__.py contains the code, we should avoid double loading.
    if modname.lower() != '__init__':
        modname_parts.append(modname)

    fullmodname = '.'.join(modname_parts)

    mod = importlib.import_module(fullmodname)

    func = getattr(mod, entry_point, None)
    if func is None or not callable(func):
        raise RuntimeError(
            f'cannot load function {name}: function {entry_point}() is not '
            f'present in {rel_script_path}')

    return func


@attach_message_to_exception(
    expt_type=ImportError,
    message=f'Troubleshooting Guide: {MODULE_NOT_FOUND_TS_URL}',
    debug_logs='Error in index_function_app. '
               f'Sys Path: {sys.path}, Sys Module: {sys.modules},'
               'python-packages Path exists: '
               f'{os.path.exists(CUSTOMER_PACKAGES_PATH)}')
def index_function_app(function_path: str):
    module_name = pathlib.Path(function_path).stem
    imported_module = importlib.import_module(module_name)

    from azure.functions import FunctionRegister
    app: Optional[FunctionRegister] = None
    for i in imported_module.__dir__():
        if isinstance(getattr(imported_module, i, None), FunctionRegister):
            if not app:
                app = getattr(imported_module, i, None)
            else:
                raise ValueError(
                    f"More than one {app.__class__.__name__} or other top "
                    f"level function app instances are defined.")

    if not app:
        raise ValueError("Could not find top level function app instances in "
                         f"{SCRIPT_FILE_NAME}.")

    return app.get_functions()
