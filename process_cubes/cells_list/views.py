from django.shortcuts import render
from import_xes.models import Attribute, Dimension, ProcessCube, EventLog
from itertools import product, chain
from slice_dice.models import Slice, Dice
from bson.json_util import dumps
from django.http import JsonResponse
import json
from datetime import datetime

# Create your views here.


def get_dim_values(dimension):
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
        values_lists = [a.values for a in attributes]

    print(values_lists)
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

    dim_values_list = [get_dim_values(dim) for dim in dimensions]

    value_combinations = list(product(*dim_values_list))
    value_combinations = [list(chain.from_iterable(vs))
                               for vs in value_combinations]

    return JsonResponse(value_combinations, safe=False)


def model(request, log_id, cube_id):
    values = request.POST.get("values")
    if(values == None):
        values = "{}"
    values = json.loads(values)
    
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
                values_[queryname] = values[key]
            else:
                name = attribute.name
                if(attribute.parent == "trace"):
                    name = 'trace:' + name

                values_[name] = values[key]

    values_['log'] = log_id
    values = values_

    # Construct datetime object to filter with pymongo
    time_format = "%Y-%m-%d %H:%M:%S.%f"
    if('time:timestamp' in values):
        if("." not in values['time:timestamp']):
            time_format = time_format[:-3]

        values['time:timestamp'] = datetime.strptime(
            values['time:timestamp'], time_format)

    time_format = "%Y-%m-%d %H:%M:%S.%f"
    if('trace:time:timestamp' in values):
        if("." not in values['time:timestamp']):
            time_format = time_format[:-3]
        values['trace:time:timestamp'] = datetime.strptime(
            values['trace:time:timestamp'], time_format)

    return JsonResponse(values, safe=False)
