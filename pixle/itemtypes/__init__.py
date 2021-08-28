from __future__ import annotations
from abc import ABCMeta, abstractmethod
from typing import Callable, Any, Union
from dearpygui.dearpygui import (
    get_item_info,
    delete_item,
    generate_uuid,
    configure_item,
    get_item_configuration,
    get_item_state,
    set_value as dpg_set_value,
    get_value,
    move_item,
)


__all__ = ["Item"]


def _set_parent(item: Item, parent):
    item_id = item._id
    if item._is_initialized:
        parent = int(parent)
        current_parent = get_item_info(item_id)["parent"]
        # If the value would be the default value (0 or None),
        # or if the parent wouldn't change, do nothing.
        if not parent or parent == current_parent:
            return None

        return move_item(item_id, parent=int(parent))
    item._staged_configuration["parent"] = parent


def _set_value(item: Item, value: Any):
    if item._is_initialized:
        return dpg_set_value(item._id, value)
    item._staged_configuration["default_value"] = value


_SET_CONFIG = {
    "parent": _set_parent,
    "value": _set_value,
    "default_value": _set_value,
}


def set_configuration(item: Item, attribute: str, value: Any):
    if func := _SET_CONFIG.get(attribute, None):
        return func(item, value)
    return configure_item(item._id, **{attribute: value})


def get_configuration(item: Item):
    # This function acts as a patched `get_item_configuration`. All
    # configuration options that an item *might* have are returned.
    item_id = item._id
    return {
        **get_item_configuration(item_id),
        "parent": get_item_info(item_id)["parent"],
        "pos": get_item_state(item_id)["pos"],
        "value": get_value(item_id),
    }



# TODO: 
#    - Include layout import/export methods.
#    - Use the weakref module instead of strong refs for the registry.

class Item(metaclass=ABCMeta):
    @abstractmethod
    def _command() -> Callable: ...
    _is_initialized = False
    _configurations: set = set()
    _staged_configuration: dict = None

    APPITEMS: dict[int,"Item"] = {}

    def __init__(self, stage: bool = False, untrack: bool = False, **kwargs):
        cls = type(self)
        # Attributes in _configurations is have unique handling.
        if not cls._configurations:
            cls._configurations = {option for option in kwargs
                                   if option != "id"}

        if parent := kwargs.pop("parent", None):
            kwargs["parent"] = int(parent)
        if kwargs.get("label", None) is None:  # empty strings have value here
            kwargs["label"] = cls.__name__

        self._id = kwargs.pop("id", generate_uuid())
        self._staged_configuration = kwargs

        # The instance won't be stored in the registry if untracked.
        if not untrack:
            cls.APPITEMS[self._id] = self
        # Complete item initialization if the item isn't being staged.
        if not stage:
            self.commit_setup()
    
    def __int__(self):
        return self._id

    def __repr__(self):
        # These next few lines of code pretty much make up the body
        # of `Item.configuration`. However, it is possible that the method
        # could be overloaded. 
        unfiltered_config = {"id": self._id} | get_configuration(self)
        configurations = self._configurations
        config = {attr: val for attr, val in unfiltered_config.items()
                  if attr in configurations or attr == "id"}
        return (
            f"{type(self).__qualname__}("
            + f", ".join((f'{attr}={val!r}' for attr, val in config.items()))
            + f")"
        )

    def __getattr__(self, attr):
        if self._is_initialized:
            return get_configuration(self)[attr]
        return self._staged_configuration[attr]

    def __setattr__(self, attr, value):
        # Allowing special methods, descriptors, etc.
        if hasattr(type(self), attr):
            return object.__setattr__(self, attr, value)

        # Where configuration values are set depends on the item's
        # initialization state.
        configurations = self._configurations
        if attr in configurations and self._is_initialized:
            return set_configuration(self, attr, value)
        elif attr in configurations:
            self._staged_configuration[attr] = value
            return None

        object.__setattr__(self, attr, value)

    def commit_setup(self):
        value_attr = self._staged_configuration.pop("value", None)
        type(self)._command(id=self._id, **self._staged_configuration)
        if value_attr is not None:
            dpg_set_value(self._id, value_attr)
        self._is_initialized = True

        del self._staged_configuration

    @property
    def id(self):
        return self._id

    def configure(self, **config) -> None:
        """Updates the item configuration item. It is the equivelent
        of calling `setattr`, and can be used to configure multiple
        attributes.
        
        """
        [setattr(self, option, value) for option, value in config.items()]

    def configuration(self, option: str = None) -> Union[dict[str, Any], Any]:
        """Returns the item's configuration options and values
        that are internally used to manage the item. If <option>
        is included, only the value of that option will be returned.
        
        NOTE: Typically, attributes included in configuration are the
        parameters used to create an instance of an item. This will
        not include other instance attributes, and does not replace
        `getattr`.
        """
        config = get_configuration(self)
        configurations = self._configurations
        config = {attr: val for attr, val in
                  get_configuration(self).items()
                  if attr in configurations or attr == "id"}
        return config.get(option, config)

    def delete(self) -> None:
        """Deletes the item and any child items it may have.

        NOTE: Call this method instead of `del` when deleting
        items.
        """
        for child in self.children():
            try:
                child.delete()
            except SystemError:
                pass

        try:
            delete_item(self._id)
        except SystemError:
            pass

        type(self).APPITEMS.pop(self._id, None)
        del self

    def children(self) -> list["Item"]:
        """Returns a list of the item's children.
        """
        # Slot 0: mvFileExtension,
        #         mvFontRangeHint,
        #         mvNodeLink,
        #         mvAnnotation,
        #         mvDragLine,
        #         mvDragPoint,
        #         mvLegend,
        #         mvTableColumn
        # Slot 1: All other app items
        # Slot 2: Draw items
        cls = type(self)
        childs = (val for slot, val in
                  get_item_info(self._id)["children"].items()
                  if slot in (0, 1, 2))
        # Flattening list of lists.
        return [cls.APPITEMS[child]
                for c_list in childs for child in c_list]

    def handlers(self) -> list["Item"]:
        """Returns a list of event handlers that are currently registered
        to the item.
        """
        # Items in Slot 3 are event handlers.
        cls = type(self)
        return [cls.APPITEMS[handler] for handler in
                get_item_info(self._id)["children"][3]]

    def duplicate(self) -> "Item":
        """Creates a copy of the item and returns it.

        Args:
            config (keyword-only, optional): Options/values to use instead
            of the 'copied' options/values.
        """
        ...