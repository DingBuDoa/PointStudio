"""Nine-slice rounded skins for native ttk controls; retain standard widget behavior."""
import base64
from io import BytesIO
import tkinter as tk
from PIL import Image, ImageDraw


def install(app, style):
    paper, forest, green = '#FFFDF9', '#16271F', '#496B5A'
    scale = max(1.0, float(app.tk.call('tk', 'scaling')) / (96/72))
    radius = max(9, round(10*scale))
    dimension = radius*2 + 12
    images = []

    def tile(fill, outline, outside=paper):
        factor = 4
        bitmap = Image.new('RGB',(dimension*factor,dimension*factor),outside)
        draw = ImageDraw.Draw(bitmap)
        draw.rounded_rectangle((factor//2,factor//2,dimension*factor-factor//2-1,dimension*factor-factor//2-1),
                               radius=radius*factor,fill=fill,outline=outline,width=factor)
        bitmap = bitmap.resize((dimension,dimension),Image.Resampling.LANCZOS)
        stream = BytesIO()
        bitmap.save(stream,format='PNG')
        photo = tk.PhotoImage(master=app,data=base64.b64encode(stream.getvalue()).decode('ascii'))
        images.append(photo)
        return photo

    def element(name, normal, mappings=()):
        # Keep PhotoImages alive for the interpreter lifetime, including dialogs.
        style.element_create(name,'image',normal,*mappings,border=(radius,radius,radius,radius),sticky='nswe')

    for prefix, fill, edge, hover, disabled, outside in (
        ('Soft','#F7F9F5','#CFDAD1','#E5EDE5','#EDF0EB',paper),
        ('Accent',green,green,'#365442','#B0BDB4',paper),
        ('Nav',forest,forest,'#2A4032',forest,forest),
        ('SelectedNav','#31483A','#31483A','#3B5544','#31483A',forest)):
        element(prefix+'.round',tile(fill,edge,outside),
                [('disabled',tile(disabled,disabled,outside)),
                 ('pressed',tile(hover,hover,outside)),
                 ('active',tile(hover,hover,outside)),
                 ('focus',tile(fill,'#A0BFA9' if prefix in ('Accent','Nav','SelectedNav') else '#496B5A',outside))])
        name = {'Soft':'TButton','Accent':'Accent.TButton','Nav':'Nav.TButton','SelectedNav':'Selected.Nav.TButton'}[prefix]
        style.layout(name,[(prefix+'.round',{'sticky':'nswe','children':[
            ('Button.padding',{'sticky':'nswe','children':[('Button.label',{'sticky':'nswe'})]})]})])
    style.configure('Nav.TButton',background=forest,foreground='#C1CEC1',anchor='w',
                    font=('Microsoft YaHei UI',12),padding=(14,13),borderwidth=0)
    style.map('Nav.TButton',foreground=[('active','#F5E4B7')])
    style.configure('Selected.Nav.TButton',foreground='#F5E4B7',font=('Microsoft YaHei UI',12,'bold'))

    element('RoundedField',tile('#F8FAF6','#CFDAD1'),
            [('disabled',tile('#EDF0EB','#DFE5DF')),('focus',tile('#F8FAF6',green))])
    style.layout('TEntry',[('RoundedField',{'sticky':'nswe','children':[
        ('Entry.padding',{'sticky':'nswe','children':[('Entry.textarea',{'sticky':'nswe'})]})]})])
    style.configure('TButton',padding=(13,5))
    style.configure('Accent.TButton',padding=(13,5))
    style.configure('TEntry',borderwidth=0,padding=(11,4))
    style.layout('TCombobox',[('RoundedField',{'sticky':'nswe','children':[
        ('Combobox.padding',{'sticky':'nswe','children':[
            ('Combobox.downarrow',{'side':'right','sticky':'ns'}),
            ('Combobox.textarea',{'sticky':'nswe'})]})]})])
    style.configure('TCombobox',borderwidth=0,padding=(11,4),arrowsize=14)
    element('RoundedGroup',tile(paper,'#D7E1D7'))
    style.layout('TLabelframe',[('RoundedGroup',{'sticky':'nswe'})])
    style.layout('Card.TLabelframe',[('RoundedGroup',{'sticky':'nswe'})])
    element('RoundedTable',tile('#F8FAF7','#D5DFD6'))
    style.layout('Table.TFrame',[('RoundedTable',{'sticky':'nswe'})])
    style.configure('Table.TFrame',background='#F8FAF7',padding=10)
    # References must belong to the root, otherwise Tk renders blank elements.
    app._rounded_theme_images = images
