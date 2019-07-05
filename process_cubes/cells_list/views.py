from django.shortcuts import render
from import_xes.models import Attribute, Dimension, ProcessCube, EventLog
from dimension_editor.models import DateHierarchy, NumericalHierarchy
from itertools import product, chain
from slice_dice.models import Slice, Dice
from bson.json_util import dumps
from django.http import JsonResponse, HttpResponse
import json
from pymongo import MongoClient
from process_cubes.settings import DATABASES
from datetime import datetime
import math
import os
from time import time
from itertools import groupby
from bson.objectid import ObjectId  
##
from pm4py.objects import log as log_lib
from pm4py.algo.discovery.alpha import factory as alpha_miner
from pm4py.visualization.petrinet import factory as pn_vis_factory
from pm4py.algo.discovery.inductive import factory as inductive_miner
from pm4py.visualization.process_tree import factory as pt_vis_factory
from pm4py.algo.discovery.heuristics import factory as heuristics_miner
from pm4py.visualization.heuristics_net import factory as hn_vis_factory
#


def get_dim_values(dimension):
    attributes = dimension.attributes.all()
    values_lists = []
    skips = []
    for attribute in attributes:
        step = 1
        if(attribute.dtype == "int" or attribute.dtype == "float"):
            try:
                hierarchy = NumericalHierarchy.objects.get(
                    attribute=attribute, dimension=dimension)
                step = hierarchy.step_size
                skips.append(hierarchy.step_size)
            except NumericalHierarchy.DoesNotExist:
                skips.append(1)
        elif(attribute.dtype == "date"):
            try:
                hierarchy = DateHierarchy.objects.get(
                    attribute=attribute, dimension=dimension)
                step = hierarchy.step_size
                skips.append(hierarchy.step_size)
            except DateHierarchy.DoesNotExist:
                skips.append(1)

        orig_values = sorted(attribute.values)
        range_values = []
        step = int(step)
        num_values = math.ceil(len(orig_values) / step)
        for i in range(num_values):
            lower = orig_values[i * step]
            if(step > 1):
                upper_ind = (i + 1) * step
                if(upper_ind >= len(orig_values)):
                    upper_ind = len(orig_values) - 1

                upper = orig_values[upper_ind]
                range_values.append('{} to {}'.format(lower, upper))
            else:
                range_values.append(str(lower))

        values_lists.append(range_values)

    values = list(product(*values_lists))
    values = [list(v) for v in values]

    return values


def get_restricted_dim_values(dimension):
    attributes = dimension.attributes.all()

    d_slice = Slice.objects.filter(dimension=dimension)
    d_dice = Dice.objects.filter(dimension=dimension)

    if(d_slice.exists()):
        restrictions = d_slice[0].value.values
        values = {r.attribute.pk: r.value for r in restrictions}
        values_lists = [[values[a.pk]] for a in attributes]
    elif(d_dice.exists()):
        restrictions = d_dice[0].values
        values = {a.pk: [] for a in attributes}
        for dr in restrictions:
            for ar in dr.values:
                values[ar.attribute.pk].append(ar.value)

        values_lists = [values[a.pk] for a in attributes]
    else:
        values_lists = []
        for attribute in attributes:
            step = 1
            if(attribute.dtype == "int" or attribute.dtype == "float"):
                try:
                    hierarchy = NumericalHierarchy.objects.get(
                        attribute=attribute, dimension=dimension)
                    step = hierarchy.step_size
                except NumericalHierarchy.DoesNotExist:
                    step = 1
            elif(attribute.dtype == "date"):
                try:
                    hierarchy = DateHierarchy.objects.get(
                        attribute=attribute, dimension=dimension)
                    step = hierarchy.step_size
                except DateHierarchy.DoesNotExist:
                    step = 1

            step = int(step)
            orig_values = sorted(attribute.values)
            range_values = []

            num_values = math.ceil(len(orig_values) / step)
            for i in range(num_values):
                lower = orig_values[i * step]
                if(step > 1):
                    upper_ind = (i + 1) * step
                    if(upper_ind >= len(orig_values)):
                        upper_ind = len(orig_values) - 1

                    upper = orig_values[upper_ind]
                    range_values.append('{} to {}'.format(lower, upper))
                else:
                    range_values.append(str(lower))

            values_lists.append(range_values)

    values = list(product(*values_lists))
    values = [list(v) for v in values]

    return values


def list_cells(request, log_id, cube_id):
    logs = EventLog.objects.all()
    log = EventLog.objects.get(pk=log_id)
    cubes = ProcessCube.objects.filter(log=log)
    cube = ProcessCube.objects.get(pk=cube_id)
    dimensions = Dimension.objects.filter(cube=cube)

    return render(request, "cells_list/cells_list.html", {
        'log': log,
        'cube': cube,
        'logs': logs,
        'cubes': cubes,
        'dimensions': dimensions,
    })


def get_cells(request, log_id, cube_id):
    log = EventLog.objects.get(pk=log_id)
    cubes = ProcessCube.objects.filter(log=log)
    cube = ProcessCube.objects.get(pk=cube_id)
    dimensions = Dimension.objects.filter(cube=cube)

    dim_values_list = [get_restricted_dim_values(dim) for dim in dimensions]

    value_combinations = list(product(*dim_values_list))
    value_combinations = [list(chain.from_iterable(vs))
                          for vs in value_combinations]

    return JsonResponse(value_combinations, safe=False)


def model(request, log_id, cube_id):
    values = request.GET.get("values")
    if(values == None):
        values = "{}"
    values = json.loads(values)

    def convert(value, dtype):
        if(dtype == 'float'):
            return float(value)
        elif(dtype == 'int'):
            return int(value)
        elif(dtype == 'date'):
            return convert_date(value)
        elif(dtype == 'bool'):
            return bool(value)
        else:
            return value

    def convert_date(value):
        # Construct datetime object to filter with pymongo
        time_format = "%Y-%m-%dT%H:%M:%S.%f"
        time_format = "%Y-%m-%d %H:%M:%S.%f"
        if("." not in value):
            time_format = time_format[:-3]

        return datetime.strptime(value, time_format)

    algo = request.GET.get("algorithm")

    values_ = {}

    # Convert to attribute id to name like it is in the events.
    values_ = {}
    for key in values:
        if(key != 'log'):
            attribute = Attribute.objects.get(pk=key)
            if(":" in attribute.parent):
                parent = attribute.parent.split(':')[0]
                d_name = attribute.parent.split(':')[1]
                name = attribute.name

                # Query for elements of dictionary
                queryname = parent + ":" + d_name + ".children." + name
            else:
                queryname = attribute.name
                if(attribute.parent == "trace"):
                    queryname = 'trace:' + queryname

            if("to" in values[key]):
                lower = values[key].split("to")[0].strip()
                upper = values[key].split('to')[1].strip()

                lower = convert(lower, attribute.dtype)
                upper = convert(upper, attribute.dtype)

                values_[queryname] = {'$gt': lower, '$lt': upper}
            else:
                value = convert(values[key], attribute.dtype)
                values_[queryname] = value

    values_['log'] = log_id
    values = values_

    client = MongoClient(host=DATABASES['default']['HOST'])
    db = client[DATABASES['default']['NAME']]
    trace_collection = db['traces']
    event_collection = db['events']

    t1 = time()
    all_events = event_collection.find(values)
    t2 = time()
    print("Time to get events: {}".format(t2 - t1))

    cube = ProcessCube.objects.get(pk=cube_id)
    if(cube.case_level):
        trace_ids = {e['trace:_id'] for e in all_events}
        trace_ids = list(map(lambda t: ObjectId(t), trace_ids))
        all_events = event_collection.find({'trace:_id' : {"$in": trace_ids}})

    print("Number of events: {}".format(all_events.count()))

    t1 = time()
    traces = groupby(all_events, key=lambda e: e['trace:_id'])
    t2 = time()
    print("Time to get traces: {}".format(t2 - t1))
    

    t1 = time()
    traces = [log_lib.log.Trace(g) for k, g in traces]
    t2 = time()
    print("Time to make list: {}".format(t2 - t1))

    print("Number of traces: {}".format(len(traces)))
    
    t1 = time()
    log = log_lib.log.EventLog(traces)
    t2 = time()
    print("Time to make event log: {}".format(t2 - t1))

    parameters = {"format": "svg"}

    event_log = EventLog.objects.get(pk=log_id)
    filename = str(event_log.pk) + algo + ".svg"

    t1 = time()
    if(algo == "alpha"):
        net, initial_marking, final_marking = alpha_miner.apply(log)
        gviz = pn_vis_factory.apply(
            net, initial_marking, final_marking, parameters=parameters)
        pn_vis_factory.save(gviz, filename)
    elif(algo == "inductive"):
        mine_tree = request.GET.get("mine_tree")
        if(mine_tree == 'true'):
            tree = inductive_miner.apply_tree(log)
            gviz = pt_vis_factory.apply(tree, parameters=parameters)
            pt_vis_factory.save(gviz, filename)
        else:
            net, initial_marking, final_marking = inductive_miner.apply(log)
            gviz = pn_vis_factory.apply(
                net, initial_marking, final_marking, parameters=parameters)
            pn_vis_factory.save(gviz, filename)
    elif(algo == "heuristic"):

        dependency_thresh = float(request.GET.get("dependency_thresh"))
        and_measure_thresh = float(request.GET.get("and_measure_thresh"))
        min_act_count = float(request.GET.get("min_act_count"))
        min_dfg_occurrences = float(request.GET.get("min_dfg_occurrences"))
        dfg_pre_cleaning_noise_thresh = float(
            request.GET.get("dfg_pre_cleaning_noise_thresh"))

        h_params = {'dependency_thresh': dependency_thresh,
                    'and_measure_thresh': and_measure_thresh,
                    'min_act_count': min_act_count,
                    'min_dfg_occurrences': min_dfg_occurrences,
                    'dfg_pre_cleaning_noise_thresh': dfg_pre_cleaning_noise_thresh,
                    }

        net, im, fm = heuristics_miner.apply(log, parameters=h_params)
        gviz = pn_vis_factory.apply(net, im, fm, parameters=parameters)
        pn_vis_factory.save(gviz, filename)

    t2 = time()
    print("Time pm4py: {}".format(t2 - t1))

    svg = open(filename, "rb")
    svg_content = svg.read()
    svg.close()

    # Tdelete file, it's not required anymore
    os.remove(svg.name)

    return HttpResponse(svg_content, content_type="image/svg+xml")
