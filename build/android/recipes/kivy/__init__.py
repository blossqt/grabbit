"""Kivy, without the Python packages it merely likes to have.

A recipe's ``python_depends`` are resolved with pip and installed into the APK.
Kivy asks for requests, which now depends on charset-normalizer, which
publishes an Android wheel - p4a resolves that wheel and then hands it to the
host's pip, which refuses a wheel built for another platform and stops the
build with a message about none of this.

Grabbit never goes through Kivy to reach the network (its loaders fall back to
urllib anyway), so the honest answer is not to ask for requests at all. The
rest of the list stays; it is small and pure Python.

The upstream recipe is loaded by path rather than imported, because p4a loads
this file *as* ``pythonforandroid.recipes.kivy`` - importing that name here
would find this half-built module instead.
"""

import importlib.util
import os

import pythonforandroid

_UPSTREAM = os.path.join(os.path.dirname(pythonforandroid.__file__),
                         'recipes', 'kivy', '__init__.py')
_spec = importlib.util.spec_from_file_location('grabbit_upstream_kivy', _UPSTREAM)
_upstream = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_upstream)


class GrabbitKivyRecipe(type(_upstream.recipe)):
    python_depends = [name for name in type(_upstream.recipe).python_depends
                      if name != 'requests']


recipe = GrabbitKivyRecipe()
