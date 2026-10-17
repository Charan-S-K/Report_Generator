import base64

class ieee:
  def __init__(self):

    self.template = r""" \documentclass[conference]{IEEEtran}

      \usepackage[utf8]{inputenc}
      \usepackage[utf8]{inputenc}
      \usepackage[T1]{fontenc}
      \usepackage{cite}
      \usepackage{textcomp}

      % Math and algorithms
      \usepackage{amsmath,amssymb,amsfonts}
      \usepackage{algorithmic}
      \usepackage{algorithm}

      % Figures and tables
      \usepackage{graphicx}
      \usepackage{xcolor}
      \usepackage{subcaption}
      \usepackage{booktabs}
      \usepackage{array}
      \usepackage{multirow}
      \usepackage{float}

      % Code, units, diagrams
      \usepackage{listings}
      \usepackage{siunitx}
      \usepackage{tikz}
      \usepackage{pgfplots}
      \pgfplotsset{compat=newest}

      % Links (keep last)
      \usepackage{url}
      \usepackage[colorlinks=true, linkcolor=black, citecolor=black, urlcolor=blue]{hyperref}
      %<<title>>
      \begin{document}
      \maketitle
      %<<abstract>>
      %<<insert>>
      %<<ref>>
      \end{document}"""
    self.context = []
    self.num_img = []
    self.count_title = 0
    self.count_author= 0
    self.count_section = 0
    self.count_subsection = 0
    self.count_table = 0
    self.count_image=0
    self.img = False
    self.count_e = 0
    self.images = {}

  def add_title(self,title:str):
    self.title = r"""
    \title{<<title>>}
    %<<author>>
    """

    sam = self.title.replace('<<title>>',title)
    self.template = self.template.replace('%<<title>>',sam)
    self.context.append(f"<<title{self.count_title}>>")
    self.count_title+=1
    #print(self.template)

  def add_author(self,number:int,names:list,affi:list):
    self.main_author = r"""
    \author{
    %<<here>>
    }
    """
    self.author = r"""
    \IEEEauthorblockN{<<name>>}
    \IEEEauthorblockA{<<affialative>>}\\
    """
    block= r""""""

    for i in range(number):
      sam = self.author.replace('<<name>>',names[i])
      sam = sam.replace('<<affialative>>',affi[i])
      #self.main_author = self.main_author.replace('%<<here>>',sam)
      if i !=number-1:
        block += (sam+r'\and')
      else:
        block +=sam
    main_author = self.main_author.replace('%<<here>>',block)

    self.template = self.template.replace('%<<author>>',main_author)
    self.context.append(f'%<<author{self.count_author}>>')
    self.count_author+=1
    #print(self.template)

  def add_section(self,name:str,contain:str):
    self.section = r"""
    \section{<<name>>}
    %<<contain>>
    %<<subsection>>
    %<<insert>>
    """
    sam = self.section.replace('<<name>>',name)
    sam = sam.replace('%<<contain>>',contain)
    self.template = self.template.replace('%<<insert>>',sam)
    self.context.append(f'%section{self.count_section}>>')
    self.count_section+=1
    #print(self.template)

  def add_abstract(self,contain = '%<<abstract>>',keyword = None):
    self.abstract = r"""
    \begin{abstract}
    %<<contain>>
    \end{abstract}
    """
    self.key = r"""
    \begin{IEEEkeywords}
    <<keyword>>
    \end{IEEEkeywords}"""
    sam1 = None
    if keyword is not None:
      sam1 = self.key.replace('<<keyword>>',keyword)
    sam = self.abstract.replace('%<<contain>>',contain)+sam1
    self.template = self.template.replace('%<<abstract>>',sam)
    self.context.append('%<<abstract>>')
    #print(self.template)

  def add_subsection(self,contain:list,names:list):
    self.subsection = r"""
    \subsection{<<name>>}
    %<<contains>>
    """
    block = r""""""
    for i in range(len(names)):
      sam = self.subsection.replace('<<name>>',names[i])
      if contain is not None:
        sam = sam.replace('%<<contains>>',contain[i])
      block+=sam
    self.template = self.template.replace('%<<subsection>>',block)
    self.context.append(f'%<<subsection{self.count_subsection}>>')
    self.count_subsection+=1
    #print(self.template)

  def add_rows(self,head : list , data:list):
    self.colmun = [r"\textbf{<<h>>}"]
    self.row = [r""]

    col = self.colmun*len(head)
    row = self.row*len(data)

    for i in range(len(head)):
      col[i] = col[i].replace('<<h>>',head[i])
    for i in range(len(data)):
      row[i]='&'.join(map(str,data[i]))
    return col,row

  def add_table(self,columns:int,head:list,data : list,caption=None,label=None):
    self.table = r"""
    \begin{table}[htbp]
    %<<caption>>
    %<<label>>
    \centering
    \begin{tabular}{<<columns>>}
    \hline
    %<<head>>
    %<<row>>
    \end{tabular}
    \end{table}
    %<<insert>>
    """
    self.caption = r"\caption{<<caption>>}"
    self.label = r"\label{tab:<<label>>}"
    col,row = self.add_rows(head,data)
    sam = self.table.replace('%<<caption>>',self.caption.replace('<<caption>>',caption))
    sam = sam.replace('%<<label>>',self.label.replace('<<label>>',label))
    c = '|c'*columns+'|'
    sam = sam.replace('<<columns>>',c)
    col = '&'.join(col)
    sam = sam.replace('%<<head>>',(col+r'\\ \hline'))
    for i in range(len(data)):
      sam = sam.replace('%<<row>>',(row[i]+r'\\ \hline  %<<row>>'))
    self.template = self.template.replace('%<<insert>>',sam)
    self.context.append(f'%<<table{self.count_table}>>')
    self.count_table+=1
    #print(self.template)

  def add_reference(self,num:int,name:list,data:list,link:list):
    self.ref = r"""
    \begin{thebibliography}{00}
    %<<ref>>
    \end{thebibliography}
    """
    self.item = r"""\bibitem{<<n>>}
    %<<ref>> %<<link>>
    """
    block = r""""""
    for i in range(num):
      sam = self.item.replace('<<n>>',name[i])
      sam = sam .replace('%<<ref>>',data[i])
      if link[i] is not None:
        sam = sam.replace('%<<link>>',link[i])
      block+=sam
    sam = self.ref.replace('%<<ref>>',block)
    self.template = self.template.replace('%<<ref>>',sam)
    print(self.get_template())

  def add_equation(self,equation:list):
    self.equa = r"""
    \begin{equation}
    %<<e>>
    \end{equation}
    %<<insert>>
    """
    block = r""""""
    for i in equation:
      block = i + r' \\ '+r"""
      """
    self.equa = self.equa.replace('%<<e>>',block)
    self.template = self.template.replace('%<<insert>>',self.equa)
    self.context.append(f'%<<equation{self.count_e}>>')
    self.count_e+=1

  def add_uploaded_image(self, data: bytes, caption: str, fmt: str, width: str = "8cm"):
    """fmt comes from the UI toggle: 'jpg' or 'png'."""
    fmt = fmt.lower().lstrip(".").replace("jpeg", "jpg")
    if fmt not in ("jpg", "png"):
      raise ValueError("Format must be jpg or png")

    ok = (fmt == "png" and data[:8] == b"\x89PNG\r\n\x1a\n") or \
         (fmt == "jpg" and data[:3] == b"\xff\xd8\xff")
    if not ok:
      raise ValueError(f"File is not a valid {fmt.upper()} image")

    key = f"image{self.count_image + 1}"          # image1, image2, ...
    self.images[key] = base64.b64encode(data).decode()
    self.add_image(key, caption, width, fmt, caption)   # add_image advances count_image
    return key

  def get_payload(self):
    return {"texFileContent": self.template, **self.images}

  def add_image(self,name:str,title:str,width:str,type:str,cap:str):
    self.img = True
    self.format = r"""\begin{figure}[htbp]
    \centering
    \advGetImage{key = <<img>>, width=<<w>>, type = <<IMGTYPE>>}
    \caption{<<cap>>}
    \label{fig:<<fig>>}
    \end{figure}
    %<<insert>>
    """
    sam = self.format.replace('<<w>>', width)
    sam = sam.replace('<<IMGTYPE>>', type)
    sam = sam.replace('<<fig>>', name)             # label follows the key: fig:image1
    sam = sam.replace('<<cap>>', cap)
    sam = sam.replace('<<img>>', name)
    self.context.append(f'%<<image{self.count_image}>>')
    self.num_img.append(name)
    self.count_image += 1
    self.template = self.template.replace('%<<insert>>', sam, 1)

    

  def get_context(self):
    return self.context


  def get_template(self):
    return self.template

