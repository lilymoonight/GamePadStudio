from PySide6.QtWidgets import QWidget
from gamepadstudio.studio_core import ConfigStore


class MappingOwner(QWidget):
    """UI harness with no SDL device, OS output, IPC or native driver access."""
    def __init__(self, root):
        super().__init__()
        self.store=ConfigStore(root); self.config=self.store.data; self.snapshot=None
        self.store.activate_controller(None)
        self.edits=[]; self.changes=[]
    def mapping_change(self, change):
        self.changes.append(change); self.store.apply_mapping_change(change,self.snapshot)
        if hasattr(self,'page'): self.page.refresh_display()
    def change_profile(self,name):
        if name and name!=self.config['active_profile']: self.mapping_change({'op':'select','profile':name})
    def edit_mapping(self,*args,**kwargs): self.edits.append((args,kwargs))
    def duplicate_profile(self): pass
    def reset_profile(self): pass
    def navigate(self,index): pass
    def setting(self,key,value): self.config[key]=value;self.store.save()
